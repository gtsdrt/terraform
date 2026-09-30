"""Publish actions and addresses only, never Terraform attribute/output values."""
import json
import sys


def summary(document):
    rows = []
    for resource in document.get("resource_changes", []):
        actions = resource["change"]["actions"]
        if actions == ["no-op"]:
            continue
        address = resource["address"].replace("|", "\\|").replace("\n", " ").replace("`", "")
        rows.append(f"| `{address}` | {', '.join(actions)} |")
    return "\n".join(["### Terraform resource actions", "",
                      "Attribute values and output values are intentionally omitted.", "",
                      "| Resource | Actions |", "|---|---|", *rows,
                      "", f"Changed resources: {len(rows)}", ""])


if __name__ == "__main__":
    print(summary(json.load(sys.stdin)))
