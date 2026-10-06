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

    def prepare_with_metadata(self, directory, apply=False, runs=None, groups=None, roles=None):
        (Path(directory) / 'tf-foundation').mkdir()
        def metadata(*args):
            if args == ('account', 'show'):
                return self.account
            if args[:3] == ('ad', 'sp', 'show'):
                return next(service for service in self.principals.values() if service['appId'] == args[-1])
            if args[:3] == ('storage', 'account', 'show'):
                return {'id': '/existing-state-account'}
            if args == ('group', 'list'):
                return groups or []
            if args == ('role', 'assignment', 'list', '--all'):
                return roles or []
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

    def existing_objects(self):
        config = fresh.configuration(self.variables, self.account, self.principals)
        groups, roles = [], []
        for index, (key, name) in enumerate(fresh.GROUP_NAMES.items(), 1):
            scope = f'/subscriptions/{self.subscription}/resourceGroups/{name}'
            groups.append({'name': name, 'id': scope, 'location': 'westeurope'})
            roles.append({'scope': scope, 'principalId': config['principal_ids']['test' if key == 'test' else 'prod'],
                          'principalType': 'ServicePrincipal',
                          'roleDefinitionId': f'/subscriptions/{self.subscription}/providers/Microsoft.Authorization/roleDefinitions/{fresh.CONTRIBUTOR_ID}',
                          'id': f'{scope}/providers/Microsoft.Authorization/roleAssignments/aaaaaaaa-0000-0000-0000-{index:012d}'})
        return config, groups, roles

    def test_existing_groups_and_grants_are_imported_without_changing_region(self):
        config, groups, roles = self.existing_objects()
        reader = {**roles[0], 'principalId': config['principal_ids']['plan'],
                  'roleDefinitionId': '/providers/Microsoft.Authorization/roleDefinitions/acdd72a7-3385-48ef-bd42-f606fba81ae7'}
        config, imports = fresh.adoption(config, groups, roles + [reader])
        self.assertEqual(config['location'], 'westeurope')
        self.assertEqual(len(imports), 6)
        self.assertNotIn('azurerm_role_assignment.plan_reader["prod"]', imports)
        with tempfile.TemporaryDirectory() as directory:
            self.prepare_with_metadata(directory, groups=groups, roles=roles + [reader])
            source = Path(directory) / 'tf-foundation/imports.local.tf'
            self.assertEqual(source.stat().st_mode & 0o777, 0o600)
            self.assertEqual(source.read_text().count('import {'), 6)
            self.assertEqual(json.loads((source.parent / 'foundation.tfvars.local.json').read_text())['location'], 'westeurope')

    def test_existing_objects_from_wrong_scopes_or_principals_are_not_adopted(self):
        config, groups, roles = self.existing_objects()
        wrong = [{**role, 'principalId': config['principal_ids']['plan']} for role in roles]
        _, imports = fresh.adoption(config, groups, wrong)
        self.assertEqual(len(imports), 3)
        with self.assertRaisesRegex(ValueError, 'another subscription'):
            fresh.adoption(config, [{**groups[0], 'id': '/subscriptions/another/resourceGroups/terraform-prod-v2'}], roles)

    def test_conditional_grants_and_mixed_group_regions_require_review(self):
        config, groups, roles = self.existing_objects()
        with self.assertRaisesRegex(ValueError, 'conditional'):
            fresh.adoption(config, groups, [{**roles[0], 'condition': 'restricted'}])
        with self.assertRaisesRegex(ValueError, 'different locations'):
            fresh.adoption(config, [{**groups[0], 'location': 'norwayeast'}, groups[1]], roles)

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
