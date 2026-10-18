"""Static cross-layer permission preflight for workload plans.

The workload identity has resource-group Contributor plus a few narrowly scoped
custom roles. It cannot create subscription-scoped resources or manage locks in
resource groups without the Lock Manager role. Those classes of change belong in
tf-foundation, which the administrator applies separately. Catch them in the plan
job before the plan is approved and applied, instead of failing the prod apply.

This check is deliberately static (it reads the plan JSON and environment only),
so it needs no Azure credentials and cannot be used to bypass the environment gate.
"""
import argparse
import json
import os
import sys

# Resource types that need subscription-level or Authorization write rights the
# workload identity does not have. Keep them in tf-foundation.
FORBIDDEN_TYPES = {
    "azurerm_security_center_subscription_pricing": "Defender pricing is subscription-scoped",
    "azurerm_role_definition": "custom role definitions need Authorization write",
    "azurerm_role_assignment": "role assignments need Authorization write",
    "azurerm_subscription": "subscription resources are not workload-managed",
    "azurerm_management_group": "management group resources are not workload-managed",
    "azurerm_policy_definition": "policy definitions are subscription/management-group scoped",
    "azurerm_subscription_policy_assignment": "policy assignments are subscription-scoped",
}
LOCK_TYPE = "azurerm_management_lock"
# module -> resource group that has the Lock Manager role assigned (prod only)
LOCK_MODULES = {"module.azlandingzone": "azlandingzone-v2"}


def module_of(address):
    parts = address.split(".")
    if len(parts) >= 2 and parts[0] == "module":
        return ".".join(parts[:2])
    return ""


def evaluate(document, environment, plan_identity=None, prod_identity=None):
    errors = []
    if plan_identity and prod_identity and plan_identity.lower() == prod_identity.lower():
        errors.append("plan identity must differ from the prod apply identity")
    for resource in document.get("resource_changes", []):
        change = resource.get("change", {})
        actions = change.get("actions") or []
        if actions in (["no-op"], ["read"]):
            continue
        address = resource.get("address", "")
        kind = resource.get("type", "")
        if kind in FORBIDDEN_TYPES:
            errors.append(f"{address}: {FORBIDDEN_TYPES[kind]} (move it to tf-foundation)")
        if kind == "azurerm_monitor_diagnostic_setting":
            target = (change.get("after") or {}).get("target_resource_id")
            if isinstance(target, str) and target.startswith("/subscriptions/") and "/resourceGroups/" not in target:
                errors.append(f"{address}: subscription-scoped diagnostic settings belong in tf-foundation")
        if kind == LOCK_TYPE:
            if environment != "tf":
                errors.append(f"{address}: management locks are only enabled for the prod root")
            elif module_of(address) not in LOCK_MODULES:
                allowed = ", ".join(sorted(LOCK_MODULES))
                errors.append(f"{address}: no Lock Manager role for this module; only {allowed} is granted")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", choices=("tf", "tf-test"), required=True)
    args = parser.parse_args()
    document = json.load(sys.stdin)
    errors = evaluate(document, args.environment,
                      os.environ.get("ARM_CLIENT_ID"), os.environ.get("PROD_CLIENT_ID"))
    if errors:
        for error in errors:
            print(f"::error::Permission preflight: {error}")
        return 1
    print("Permission preflight passed: no subscription-scoped or ungranted-lock resources.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
