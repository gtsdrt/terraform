import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / ".github/scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


drift = load("drift_plan")
preflight = load("preflight_permissions")


def change(kind, address="module.azlandingzone.example", actions=("create",), after=None):
    return {"address": address, "type": kind,
            "change": {"actions": list(actions), "after": after or {}}}


class DriftPlanTests(unittest.TestCase):
    def test_exit_codes_are_classified(self):
        self.assertEqual(drift.classify(0), "clean")
        self.assertEqual(drift.classify(2), "drift")
        for code in (1, 3, 127):
            self.assertEqual(drift.classify(code), "error")

    def test_chdir_uses_the_equals_form(self):
        # Terraform rejects "-chdir DIR"; the option must be a single "-chdir=DIR".
        command = drift.plan_command("tf", "terraform.tfvars", "/tmp/drift.tfplan")
        self.assertIn("-chdir=tf", command)
        self.assertNotIn("-chdir", command)
        self.assertIn("-detailed-exitcode", command)
        self.assertEqual(command[0], "terraform")
        shown = drift.show_command("tf", "/tmp/drift.tfplan")
        self.assertIn("-chdir=tf", shown)
        self.assertNotIn("-chdir", shown)

    def test_report_is_published_to_the_log_and_the_summary(self):
        # `gh run view --log` cannot see the Summary tab, so drift must print the
        # sanitized report to stdout as well instead of only pointing at "above".
        document = {"resource_changes": [{
            "address": "azurerm_key_vault.kv", "type": "azurerm_key_vault",
            "change": {"actions": ["create"], "after": {"rbac_authorization_enabled": True}}}]}
        with tempfile.TemporaryDirectory() as temporary:
            summary = Path(temporary) / "summary.md"
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed):
                text = drift.publish(document, str(summary))
            self.assertIn("azurerm_key_vault.kv", text)
            self.assertIn("rbac_authorization_enabled", text)
            self.assertEqual(printed.getvalue(), text + "\n")
            self.assertEqual(summary.read_text(encoding="utf-8"), text)
        # No summary path (local runs) must not crash.
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(drift.publish(document, ""))


class NotDeployedTests(unittest.TestCase):
    @staticmethod
    def document(*actions, prior=None):
        return {
            "prior_state": {"values": {"root_module": prior or {}}},
            "resource_changes": [
                {"address": f"resource.{index}", "type": "azurerm_virtual_network",
                 "change": {"actions": list(action)}}
                for index, action in enumerate(actions)],
        }

    def test_an_entirely_absent_environment_is_not_drift(self):
        self.assertTrue(drift.not_deployed(self.document(("create",), ("create",), prior={})))

    def test_missing_prior_state_is_treated_as_an_empty_state(self):
        document = self.document(("create",))
        del document["prior_state"]
        self.assertTrue(drift.not_deployed(document))

    def test_a_partially_deployed_state_is_still_drift(self):
        prior = {"resources": [{"address": "azurerm_virtual_network.kept"}]}
        self.assertFalse(drift.not_deployed(self.document(("create",), prior=prior)))

    def test_nested_modules_count_as_deployed(self):
        prior = {"child_modules": [{"resources": [{"address": "module.network.azurerm_virtual_network.main"}]}]}
        self.assertFalse(drift.not_deployed(self.document(("create",), prior=prior)))

    def test_only_creates_may_be_classified_as_not_deployed(self):
        for actions in (("delete",), ("update",), ("delete", "create")):
            with self.subTest(actions=actions):
                self.assertFalse(drift.not_deployed(self.document(actions)))

    def test_noop_entries_are_ignored(self):
        # A no-op cannot occur with an empty state, but it must never disqualify a
        # create-only plan if Terraform ever emits one alongside it.
        self.assertTrue(drift.not_deployed(self.document(("no-op",), ("create",))))

    def test_an_empty_plan_is_not_classified_as_not_deployed(self):
        self.assertFalse(drift.not_deployed(self.document()))


class DriftWorkflowTests(unittest.TestCase):
    def test_only_the_persistent_root_is_checked(self):
        # tf-test is destroyed after every deploy and every 6 hours, so including it
        # would make the scheduled run red forever ("not deployed", not drift).
        workflow = (ROOT / ".github/workflows/drift-detection.yml").read_text(encoding="utf-8")
        matrix = [line.strip() for line in workflow.splitlines() if line.strip().startswith("dir:")]
        self.assertEqual(matrix, ["dir: [tf]"])


class PreflightTests(unittest.TestCase):
    def test_normal_resource_passes(self):
        document = {"resource_changes": [change("azurerm_key_vault")]}
        self.assertEqual(preflight.evaluate(document, "tf"), [])

    def test_lock_in_lock_manager_module_is_allowed(self):
        document = {"resource_changes": [
            change("azurerm_management_lock", "module.azlandingzone.azurerm_management_lock.key_vault")]}
        self.assertEqual(preflight.evaluate(document, "tf"), [])

    def test_lock_outside_lock_manager_module_is_rejected(self):
        document = {"resource_changes": [
            change("azurerm_management_lock", "module.network.azurerm_management_lock.main")]}
        self.assertTrue(preflight.evaluate(document, "tf"))

    def test_lock_in_test_root_is_rejected(self):
        document = {"resource_changes": [
            change("azurerm_management_lock", "module.azlandingzone.azurerm_management_lock.key_vault")]}
        self.assertTrue(preflight.evaluate(document, "tf-test"))

    def test_subscription_scoped_resources_are_rejected(self):
        for kind in ("azurerm_security_center_subscription_pricing",
                     "azurerm_role_definition", "azurerm_role_assignment"):
            with self.subTest(kind=kind):
                self.assertTrue(preflight.evaluate({"resource_changes": [change(kind)]}, "tf"))

    def test_subscription_diagnostic_is_rejected(self):
        document = {"resource_changes": [change(
            "azurerm_monitor_diagnostic_setting",
            after={"target_resource_id": "/subscriptions/11111111-1111-1111-1111-111111111111"})]}
        self.assertTrue(preflight.evaluate(document, "tf"))

    def test_resource_group_diagnostic_is_allowed(self):
        document = {"resource_changes": [change(
            "azurerm_monitor_diagnostic_setting",
            after={"target_resource_id": "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/azlandingzone-v2/providers/Microsoft.KeyVault/vaults/kv"})]}
        self.assertEqual(preflight.evaluate(document, "tf"), [])

    def test_plan_and_prod_identity_must_differ(self):
        errors = preflight.evaluate({"resource_changes": []}, "tf", "same", "same")
        self.assertTrue(errors)

    def test_noop_and_read_changes_are_ignored(self):
        for actions in (["no-op"], ["read"]):
            with self.subTest(actions=actions):
                document = {"resource_changes": [change(
                    "azurerm_security_center_subscription_pricing", actions=actions)]}
                self.assertEqual(preflight.evaluate(document, "tf"), [])

    def test_module_address_parsing(self):
        self.assertEqual(preflight.module_of("module.azlandingzone.azurerm_key_vault.this"),
                         "module.azlandingzone")
        self.assertEqual(preflight.module_of("azurerm_key_vault.this"), "")


if __name__ == "__main__":
    unittest.main()
