import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import io
import sys
import textwrap
from contextlib import redirect_stdout
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / ".github/scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


artifact = load("plan_artifact")
summary = load("plan_summary")
runner = load("tf_run")
workflows = load("check_workflows")


class SecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temp.name)
        cls.key = cls.directory / "private.pem"
        cls.cert = cls.directory / "public.pem"
        subprocess.run([os.getenv("OPENSSL_BIN", "openssl"), "req", "-x509", "-newkey",
                        "rsa:2048", "-nodes", "-keyout", str(cls.key), "-out", str(cls.cert),
                        "-days", "1", "-subj", "/CN=security-test"], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.context = patch.dict(os.environ, {
            "GITHUB_REPOSITORY": "test/terraform", "GITHUB_SHA": "a" * 40,
            "GITHUB_RUN_ID": "42", "TF_PLAN_PRIVATE_KEY": self.key.read_text()})
        self.context.start()
        self.addCleanup(self.context.stop)
        self.case = tempfile.TemporaryDirectory()
        self.addCleanup(self.case.cleanup)
        self.plan = Path(self.case.name) / "tfplan"
        self.encrypted = Path(self.case.name) / "plan.cms"
        self.output = Path(self.case.name) / "decrypted"
        self.plan.write_bytes(b"sensitive-test-plan\x00\xff")
        artifact.seal(self.plan, self.cert, self.encrypted, "tf")

    def test_exact_plan_round_trip(self):
        artifact.unseal(self.encrypted, self.cert, self.output, "tf")
        self.assertEqual(self.output.read_bytes(), self.plan.read_bytes())
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(self.plan.read_bytes(), self.encrypted.read_bytes())

    def test_replay_from_other_run_commit_repository_or_environment_is_rejected(self):
        for name in ("GITHUB_REPOSITORY", "GITHUB_SHA", "GITHUB_RUN_ID"):
            with self.subTest(name=name), patch.dict(os.environ, {name: "different"}):
                with self.assertRaises(ValueError):
                    artifact.unseal(self.encrypted, self.cert, self.output, "tf")
                self.assertFalse(self.output.exists())
        with self.assertRaises(ValueError):
            artifact.unseal(self.encrypted, self.cert, self.output, "tf-test")

    def test_modified_ciphertext_is_rejected_without_writing_plaintext(self):
        ciphertext = bytearray(self.encrypted.read_bytes())
        ciphertext[-1] ^= 1
        self.encrypted.write_bytes(ciphertext)
        with self.assertRaises(subprocess.CalledProcessError):
            artifact.unseal(self.encrypted, self.cert, self.output, "tf")
        self.assertFalse(self.output.exists())

    def test_missing_private_key_is_rejected(self):
        with patch.dict(os.environ, {"TF_PLAN_PRIVATE_KEY": ""}):
            with self.assertRaises(ValueError):
                artifact.unseal(self.encrypted, self.cert, self.output, "tf")

    def test_expired_plan_is_rejected_without_plaintext(self):
        with patch.object(artifact.time, "time", return_value=0):
            artifact.seal(self.plan, self.cert, self.encrypted, "tf")
        with patch.object(artifact.time, "time", return_value=artifact.PLAN_LIFETIME_SECONDS):
            with self.assertRaises(artifact.ExpiredPlanError):
                artifact.unseal(self.encrypted, self.cert, self.output, "tf")
        self.assertFalse(self.output.exists())

    def test_plan_purpose_and_scope_cannot_be_replayed(self):
        artifact.seal(self.plan, self.cert, self.encrypted, "tf", "destroy", "network")
        for purpose, scope in (("deploy", "all"), ("destroy", "all"), ("destroy", "azlandingzone")):
            with self.subTest(purpose=purpose, scope=scope), self.assertRaises(ValueError):
                artifact.unseal(self.encrypted, self.cert, self.output, "tf", purpose, scope)
            self.assertFalse(self.output.exists())
        artifact.unseal(self.encrypted, self.cert, self.output, "tf", "destroy", "network")
        self.assertEqual(self.output.read_bytes(), self.plan.read_bytes())

    def test_future_plan_is_rejected_without_plaintext(self):
        with patch.object(artifact.time, "time", return_value=1000):
            artifact.seal(self.plan, self.cert, self.encrypted, "tf")
        with patch.object(artifact.time, "time", return_value=900):
            with self.assertRaises(ValueError):
                artifact.unseal(self.encrypted, self.cert, self.output, "tf")
        self.assertFalse(self.output.exists())

    def test_summary_security_changes_mask_sensitive_and_unknown_fields(self):
        document = {"resource_changes": [{"address": "azurerm_network_security_rule.test", "type": "azurerm_network_security_rule", "change": {
            "actions": ["update"],
            "before": {"access": "Deny", "destination_port_range": "443", "source_address_prefix": "10.99.88.0/24"},
            "after": {"access": "Allow", "destination_port_range": "8443", "source_address_prefix": "secret-marker", "password": "secret-marker", "priority": 123},
            "after_sensitive": {"destination_port_range": True}, "after_unknown": {"priority": True}}}]}
        rendered = summary.summary(document)
        self.assertIn("Deny", rendered)
        self.assertIn("Allow", rendered)
        self.assertIn("443", rendered)
        for withheld in ("8443", "10.99.88.0/24", "secret-marker", "123"):
            self.assertNotIn(withheld, rendered)
        self.assertIn("[sensitive]", rendered)
        self.assertIn("[unknown until apply]", rendered)

    def test_summary_nested_rules_do_not_publish_arbitrary_strings(self):
        document = {"resource_changes": [{"address": "azurerm_network_security_group.test", "type": "azurerm_network_security_group", "change": {
            "actions": ["create"], "after": {"security_rule": [{"name": "secret-marker", "access": "Allow", "protocol": "Tcp", "destination_port_range": "443", "source_address_prefix": "Internet"}]},
            "after_sensitive": {}}}]}
        rendered = summary.summary(document)
        self.assertIn("Internet", rendered)
        self.assertIn("443", rendered)
        self.assertNotIn("secret-marker", rendered)
        document["resource_changes"][0]["change"]["after_sensitive"] = {"security_rule": [{"access": True}]}
        rendered = summary.summary(document)
        self.assertNotIn("Internet", rendered)
        self.assertNotIn("443", rendered)

    def test_summary_nested_network_acls_respect_sensitive_masks(self):
        document = {"resource_changes": [{"address": "azurerm_key_vault.test", "type": "azurerm_key_vault", "change": {
            "actions": ["update"], "after": {"purge_protection_enabled": False, "network_acls": [{"default_action": "Allow", "bypass": "AzureServices", "ip_rules": ["secret-marker"]}]},
            "after_sensitive": {"network_acls": [{"default_action": True}]}}}]}
        rendered = summary.summary(document)
        self.assertIn("false", rendered)
        self.assertIn("AzureServices", rendered)
        self.assertNotIn("| Allow |", rendered)
        self.assertNotIn("secret-marker", rendered)

    def test_runner_classifies_failure_and_removes_sensitive_logs(self):
        log = Path(self.case.name) / "raw.log"
        output = io.StringIO()
        with redirect_stdout(output):
            status = runner.run([sys.executable, "-c", "import sys; print('AuthorizationFailed secret-marker'); sys.exit(1)"], log)
        self.assertEqual(status, 1)
        self.assertIn("authorization", output.getvalue())
        self.assertNotIn("secret-marker", output.getvalue())
        self.assertFalse(log.exists())

    def test_runner_success_also_withholds_output_and_removes_logs(self):
        log = Path(self.case.name) / "raw.log"
        output = io.StringIO()
        with redirect_stdout(output):
            status = runner.run([sys.executable, "-c", "print('secret-marker')"], log)
        self.assertEqual(status, 0)
        self.assertNotIn("secret-marker", output.getvalue())
        self.assertFalse(log.exists())

    def test_runner_classifies_soft_delete_conflicts_and_provider_errors_without_leaks(self):
        cases = [
            ('An existing soft-deleted Key Vault exists with the Name "secret-marker", however automatically recovering this KeyVault has been disabled via the "features" block.', 'key-vault-soft-delete'),
            ('RetryableErrorDueToAnotherOperation secret-marker', 'api-conflict'),
            ('Provider produced inconsistent final plan secret-marker', 'provider-error'),
            ("Error: doesn't support update secret-marker", 'provider-error'),
        ]
        for message, category in cases:
            with self.subTest(category=category, message=message):
                log = Path(self.case.name) / 'classified.log'
                output = io.StringIO()
                with redirect_stdout(output):
                    status = runner.run([sys.executable, '-c', f'import sys; print({message!r}); sys.exit(1)'], log)
                self.assertEqual(status, 1)
                self.assertIn(f'({category})', output.getvalue())
                self.assertNotIn('secret-marker', output.getvalue())
                self.assertFalse(log.exists())

    def test_summary_explains_recovery_only_for_key_vault_create_when_enabled(self):
        resource = {'address': 'azurerm_key_vault.test', 'type': 'azurerm_key_vault',
                    'change': {'actions': ['create'], 'after': {'name': 'secret-marker'}}}
        document = {'resource_changes': [resource]}
        text = summary.summary(document, key_vault_recovery=True)
        self.assertIn('may restore', text)
        self.assertIn('retain its existing contents', text)
        self.assertNotIn('secret-marker', text)
        self.assertNotIn('may restore', summary.summary(document))
        for actions in (['no-op'], ['update'], ['delete']):
            with self.subTest(actions=actions):
                resource['change']['actions'] = actions
                self.assertNotIn('may restore', summary.summary(document, key_vault_recovery=True))
        resource['change']['actions'] = ['create']
        resource['type'] = 'azurerm_virtual_network'
        self.assertNotIn('may restore', summary.summary(document, key_vault_recovery=True))

    def test_workflow_lint_adapter_rejects_invalid_queue_combinations(self):
        valid = "concurrency:\n  group: terraform-deploy\n  cancel-in-progress: false\n  queue: max\njobs:\n  example: {}\n"
        adapted = workflows.lint_source(valid)
        self.assertEqual(len(valid.splitlines()), len(adapted.splitlines()))
        self.assertIn("jobs:\n  example: {}", adapted)
        for invalid in (valid.replace("cancel-in-progress: false", "cancel-in-progress: true"), valid.replace("  queue: max", "  queue: max\n  queue: max")):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                workflows.lint_source(invalid)
        unsupported = valid.replace("queue: max", "queue: unsupported")
        self.assertIn("queue: unsupported", workflows.lint_source(unsupported))

    def test_recovery_step_handles_pagination_cli_errors_and_manual_runs(self):
        workflow = (ROOT / ".github/workflows/cleanup-test.yml").read_text()
        script = textwrap.dedent(workflow.split("        run: |\n", 1)[1].split("      - uses:", 1)[0])
        mock = Path(self.case.name) / "gh"
        mock.write_text("#!/bin/sh\nfor arg in \"$@\"; do\n  case \"$arg\" in --jq|--template) exit 2 ;; esac\ndone\nprintf '%s\\n' \"$MOCK_GH_PAGES\"\nexit \"${MOCK_GH_STATUS:-0}\"\n")
        mock.chmod(0o755)
        cases = [
            ("workflow_run", [{"jobs": []}, {"jobs": [{"name": "deploy-test", "conclusion": "success"}]}], 0, "true"),
            ("workflow_run", [{"jobs": [{"name": "deploy-test", "conclusion": "cancelled"}]}], 0, "true"),
            ("workflow_run", [{"jobs": [{"name": "deploy-test", "conclusion": "skipped"}]}], 0, "false"),
            ("workflow_run", [{"jobs": []}], 1, None),
            ("schedule", [], 1, "true"),
            ("workflow_dispatch", [], 1, "true"),
        ]
        for event, pages, status, needed in cases:
            with self.subTest(event=event, pages=pages, status=status):
                output = Path(self.case.name) / "github-output"
                output.write_text("")
                env = {**os.environ, "PATH": f"{self.case.name}:{os.environ['PATH']}",
                       "GITHUB_EVENT_NAME": event, "GITHUB_REPOSITORY": "test/terraform",
                       "SOURCE_RUN_ID": "42", "GITHUB_OUTPUT": str(output),
                       "MOCK_GH_PAGES": json.dumps(pages), "MOCK_GH_STATUS": str(status)}
                result = subprocess.run(["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script], env=env, capture_output=True, text=True)
                if needed is None:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(output.read_text(), "")
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(output.read_text(), f"needed={needed}\n")

    def test_summary_omits_secrets_even_if_not_marked_sensitive(self):
        document = {"variables": {"password": {"value": "secret-marker"}},
                    "output_changes": {"token": {"after": "secret-marker"}},
                    "resource_changes": [{"address": "example.this", "change": {
                        "actions": ["update"], "before": {"key": "secret-marker"},
                        "after": {"key": "secret-marker"}, "after_sensitive": {}}}]}
        rendered = summary.summary(document)
        self.assertNotIn("secret-marker", rendered)
        self.assertIn("example.this", rendered)

    def test_confirmation_is_literal_and_never_evaluates_shell(self):
        script = ROOT / ".github/scripts/check_destroy_inputs.sh"
        marker = Path(self.case.name) / "injected"
        env = {**os.environ, "DESTROY_CONFIRM": f"DESTROY$(touch {marker})",
               "DESTROY_ENV": "tf", "DESTROY_SCOPE": "all"}
        self.assertNotEqual(subprocess.run(["bash", str(script)], env=env,
                                           capture_output=True).returncode, 0)
        self.assertFalse(marker.exists())
        env["DESTROY_CONFIRM"] = "DESTROY"
        self.assertEqual(subprocess.run(["bash", str(script)], env=env,
                                       capture_output=True).returncode, 0)
        env["DESTROY_ENV"] = "../../other"
        self.assertNotEqual(subprocess.run(["bash", str(script)], env=env,
                                           capture_output=True).returncode, 0)


if __name__ == "__main__":
    unittest.main()
