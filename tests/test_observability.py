import importlib.util
from pathlib import Path
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
