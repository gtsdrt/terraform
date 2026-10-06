import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('fresh', ROOT / 'scripts/prepare_fresh_start.py')
fresh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fresh)


class FreshStartTests(unittest.TestCase):
    def setUp(self):
        self.subscription = '11111111-1111-1111-1111-111111111111'
        self.tenant = '22222222-2222-2222-2222-222222222222'
        self.variables = {'TERRAFORM_AZURE_SUBSCRIPTION_ID': self.subscription, 'TERRAFORM_AZURE_TENANT_ID': self.tenant}
        self.principals = {}
        for index, kind in enumerate(fresh.KINDS, 3):
            client = f'{index:08d}-0000-0000-0000-000000000000'
            object_id = f'{index+3:08d}-0000-0000-0000-000000000000'
            self.variables[f'TERRAFORM_{kind.upper()}_AZURE_CLIENT_ID'] = client
            self.principals[kind] = {'appId': client, 'id': object_id, 'displayName': f'gtsdrt-terraform-{kind}'}
        self.account = {'id': self.subscription, 'tenantId': self.tenant}

    def prepare_with_metadata(self, directory, apply=False, runs=None):
        (Path(directory) / 'tf-foundation').mkdir()
        def metadata(*args):
            if args == ('account', 'show'):
                return self.account
            if args[:3] == ('ad', 'sp', 'show'):
                return next(service for service in self.principals.values() if service['appId'] == args[-1])
            if args[:3] == ('storage', 'account', 'show'):
                return {'id': '/existing-state-account'}
            self.fail(f'Unexpected Azure request before initialization: {args[:3]}')
        variables = json.dumps([{'name': key, 'value': value} for key, value in self.variables.items()])
        with patch.object(fresh.bootstrap, 'ROOT', Path(directory)), patch.object(fresh.bootstrap, 'call', return_value=variables), patch.object(fresh.bootstrap, 'az', side_effect=metadata), patch.object(fresh.bootstrap, 'gh_json', return_value={'workflow_runs': runs or []}):
            fresh.prepare(apply)

    def test_default_preview_only_reads_azure_and_protects_local_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            self.prepare_with_metadata(directory)
            path = Path(directory) / 'tf-foundation/foundation.tfvars.local.json'
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(path.read_text())['subscription_id'], self.subscription)

    def test_active_writer_blocks_setup_before_any_azure_write(self):
        for name in ('Terraform Deploy', 'Terraform Destroy', 'Terraform Test Recovery'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                with self.assertRaisesRegex(RuntimeError, 'Stop deployment'):
                    self.prepare_with_metadata(directory, apply=True, runs=[{'name': name, 'status': 'in_progress'}])

    def test_configuration_rejects_wrong_subscription_or_another_application(self):
        config = fresh.configuration(self.variables, self.account, self.principals)
        self.assertEqual(config['principal_ids']['plan'], self.principals['plan']['id'])
        with self.assertRaises(ValueError):
            fresh.configuration(self.variables, {**self.account, 'tenantId': self.subscription}, self.principals)
        with self.assertRaises(ValueError):
            fresh.configuration(self.variables, self.account, {**self.principals, 'prod': {**self.principals['prod'], 'displayName': 'gtsdrt-ai-api'}})
        duplicate = {**self.variables, 'TERRAFORM_TEST_AZURE_CLIENT_ID': self.variables['TERRAFORM_PLAN_AZURE_CLIENT_ID']}
        with self.assertRaises(ValueError):
            fresh.configuration(duplicate, self.account, self.principals)

    def test_existing_state_is_not_read_downloaded_or_overwritten(self):
        with patch.object(fresh.bootstrap, 'az', return_value={'exists': True}) as az:
            fresh.seed_state('tfstate-prod', 'executor-v2.tfstate')
        self.assertEqual(az.call_count, 1)
        self.assertEqual(az.call_args.args[:3], ('storage', 'blob', 'exists'))

    def test_new_state_is_empty_and_upload_refuses_overwrite(self):
        calls = []
        def az(*args):
            calls.append(args)
            if args[:3] == ('storage', 'blob', 'exists'):
                return {'exists': False}
            document = json.loads(Path(args[args.index('--file')+1]).read_text())
            self.assertEqual(document['resources'], [])
            self.assertEqual(document['serial'], 0)
            self.assertEqual(document['outputs'], {})
            self.assertEqual(args[args.index('--overwrite')+1], 'false')
            return {}
        with patch.object(fresh.bootstrap, 'az', side_effect=az):
            fresh.seed_state('tfstate-test', 'terraform-test-v2.tfstate')
        self.assertEqual(len(calls), 2)
        for old in ('executor.tfstate', 'terraform-test.tfstate'):
            with self.assertRaises(ValueError):
                fresh.seed_state('tfstate-prod', old)

    def test_temporary_data_role_is_revoked_even_when_initialization_fails(self):
        calls = []
        def az(*args):
            calls.append(args)
            if args[:3] == ('role', 'assignment', 'list'):
                return []
            if args[:3] == ('role', 'assignment', 'create'):
                return {'id': '/temporary-role'}
            return {}
        with patch.object(fresh.bootstrap, 'az', side_effect=az), patch.object(fresh.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)):
            with self.assertRaises(RuntimeError):
                with fresh.temporary_blob_access('operator', '/state-account'):
                    raise RuntimeError('simulated upload failure')
        self.assertEqual(calls[-1], ('role', 'assignment', 'delete', '--ids', '/temporary-role'))

    def test_existing_operator_role_is_retained(self):
        existing = [{'scope': '/state-account', 'roleDefinitionName': 'Storage Blob Data Contributor'}]
        with patch.object(fresh.bootstrap, 'az', return_value=existing) as az, patch.object(fresh.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)):
            with fresh.temporary_blob_access('operator', '/state-account'):
                pass
        self.assertEqual(az.call_count, 1)
