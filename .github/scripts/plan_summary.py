"""Publish resource actions and a bounded allowlist of security settings."""
import html
import json
import sys

# No arbitrary strings, IPs, names, IDs, tags, variables or output values.
RULE_FIELDS = {
    "access": {"Allow", "Deny"}, "direction": {"Inbound", "Outbound"},
    "protocol": {"*", "Tcp", "Udp", "Icmp", "Esp", "Ah"},
    "priority": (100, 4096),
    "source_address_prefix": {"*", "0.0.0.0/0", "::/0", "VirtualNetwork", "Internet", "AzureLoadBalancer"},
    "destination_address_prefix": {"*", "0.0.0.0/0", "::/0", "VirtualNetwork", "Internet", "AzureLoadBalancer"},
    "destination_port_range": "port",
}
FIELDS = {
    "azurerm_network_security_rule": RULE_FIELDS,
    "azurerm_key_vault": {
        "rbac_authorization_enabled": bool, "purge_protection_enabled": bool,
        "public_network_access_enabled": bool, "soft_delete_retention_days": (7, 90),
    },
    "azurerm_storage_account": {
        "allow_nested_items_to_be_public": bool, "shared_access_key_enabled": bool,
        "public_network_access_enabled": bool, "https_traffic_only_enabled": bool,
        "min_tls_version": {"TLS1_0", "TLS1_1", "TLS1_2"},
        "account_replication_type": {"LRS", "ZRS", "GRS", "RAGRS", "GZRS", "RAGZRS"},
    },
    "azurerm_log_analytics_workspace": {"retention_in_days": (30, 730)},
}
NETWORK_FIELDS = {"default_action": {"Allow", "Deny"}, "bypass": {"None", "AzureServices", "Logging", "Metrics"}}


def cell(value):
    return html.escape(str(value)).replace("|", "\\|").replace("\n", " ").replace("\r", " ").replace("`", "")


def protected(mask):
    if isinstance(mask, dict):
        return any(protected(x) for x in mask.values())
    if isinstance(mask, list):
        return any(protected(x) for x in mask)
    return mask is True


def safe_value(value, allowed, mask=False, unknown=False):
    if protected(mask):
        return "[sensitive]"
    if protected(unknown):
        return "[unknown until apply]"
    if value is None:
        return "[absent]"
    if allowed is bool and type(value) is bool:
        return str(value).lower()
    if isinstance(allowed, tuple) and type(value) is int and allowed[0] <= value <= allowed[1]:
        return str(value)
    if isinstance(allowed, set):
        if isinstance(value, str) and value in allowed:
            return value
        if isinstance(value, list) and all(isinstance(x, str) and x in allowed for x in value):
            return ", ".join(sorted(value))
    if allowed == "port" and isinstance(value, str):
        if value == "*":
            return "*"
        parts = value.split("-")
        if len(parts) in (1, 2) and all(x.isascii() and x.isdigit() and 0 <= int(x) <= 65535 for x in parts):
            return value
    return "[value withheld]"


def settings(resource):
    change = resource["change"]
    fields = dict(FIELDS.get(resource.get("type"), {}))
    kind = resource.get("type")
    if kind == "azurerm_network_security_group":
        # Show before/after snapshots; do not publish names or specific IPs.
        for phase in ("before", "after"):
            values = change.get(phase) or {}
            rules = values.get("security_rule") or []
            mask = change.get(f"{phase}_sensitive", {})
            if not isinstance(mask, dict) or protected(mask.get("security_rule", False)):
                yield f"security_rule ({phase})", "[sensitive]", "[sensitive]"
                continue
            unknown = change.get("after_unknown", {}).get("security_rule", False) if phase == "after" else False
            if protected(unknown):
                yield "security_rule (after)", "[unknown until apply]", "[unknown until apply]"
                continue
            for index, rule in enumerate(rules):
                for key, allowed in RULE_FIELDS.items():
                    if key in rule:
                        yield f"security_rule[{index}].{key} ({phase})", "—", safe_value(rule[key], allowed)
    for block in ("network_acls", "network_rules"):
        if kind in ("azurerm_key_vault", "azurerm_storage_account"):
            for key, allowed in NETWORK_FIELDS.items():
                fields[f"{block}.{key}"] = allowed
    for path, allowed in fields.items():
        parts = path.split(".")
        values = []
        for phase in ("before", "after"):
            value = change.get(phase) or {}
            mask = change.get(f"{phase}_sensitive", {})
            unknown = change.get("after_unknown", {}) if phase == "after" else {}
            for part in parts:
                value = value[0] if isinstance(value, list) and value else value
                mask = mask[0] if isinstance(mask, list) and mask else mask
                unknown = unknown[0] if isinstance(unknown, list) and unknown else unknown
                value = value.get(part) if isinstance(value, dict) else None
                mask = mask.get(part, False) if isinstance(mask, dict) else mask
                unknown = unknown.get(part, False) if isinstance(unknown, dict) else unknown
            values.append(safe_value(value, allowed, mask, unknown))
        if values != ["[absent]", "[absent]"]:
            yield path, *values

def summary(document):
    rows = []
    details = []
    for resource in document.get("resource_changes", []):
        actions = resource["change"]["actions"]
        if actions == ["no-op"]:
            continue
        address = cell(resource["address"])
        rows.append(f"| `{address}` | {', '.join(actions)} |")
        for field, before, after in settings(resource):
            details.append(f"| `{address}` | `{field}` | {cell(before)} | {cell(after)} |")
    return "\n".join(["### Terraform resource actions", "",
                      "Only allowlisted security settings are shown. IPs, IDs, arbitrary strings, sensitive values, variables and outputs are withheld.", "",
                      "| Resource | Actions |", "|---|---|", *rows,
                      "", f"Changed resources: {len(rows)}", "",
                      "### Security settings for review", "",
                      "| Resource | Setting | Before | After |", "|---|---|---|---|", *details, ""])


if __name__ == "__main__":
    print(summary(json.load(sys.stdin)))
