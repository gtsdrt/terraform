"""One-time administrator setup. No client secrets are created or printed.

prepare creates identities, copies existing state into private containers, and
stores plan private keys as GitHub environment secrets. retire revokes only the
old dedicated Terraform executor identity after the new configuration is ready.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
REPO = "gtsdrt/terraform"
ACCOUNT = "gtsdrtterraform2"
STATE_RG = "Terraform"
GROUPS = {"tf": ("terraform-prod", "azlandingzone"), "tf-test": ("terraform-test",)}
OLD_APP_ID = "447b08d8-e456-4c22-8599-023e248ef696"


def call(*args, data=None, capture=True):
    result = subprocess.run(args, input=data, text=True, check=True,
                            stdout=subprocess.PIPE if capture else subprocess.DEVNULL)
    return result.stdout


def az(*args):
    output = call("az", *args, "--only-show-errors", "-o", "json")
    return json.loads(output) if output.strip() else None


def gh_json(path):
    return json.loads(call("gh", "api", path))


def grant(principal, role, scope):
    existing = az("role", "assignment", "list", "--assignee", principal, "--scope", scope)
    if not any(x["scope"].lower() == scope.lower() and x["roleDefinitionName"] == role
               for x in existing):
        az("role", "assignment", "create", "--assignee-object-id", principal,
           "--assignee-principal-type", "ServicePrincipal", "--role", role, "--scope", scope)


def identity(kind, subject):
    name = f"gtsdrt-terraform-{kind}"
    apps = az("ad", "app", "list", "--display-name", name)
    if len(apps) > 1:
        raise RuntimeError(f"Multiple applications named {name}; resolve manually")
    app = apps[0] if apps else az("ad", "app", "create", "--display-name", name)
    service = az("ad", "sp", "list", "--filter", f"appId eq '{app['appId']}'")
    sp = service[0] if service else az("ad", "sp", "create", "--id", app["appId"])
    federation = {"name": "github-terraform", "issuer": "https://token.actions.githubusercontent.com",
                  "subject": subject, "audiences": ["api://AzureADTokenExchange"]}
    credentials = az("ad", "app", "federated-credential", "list", "--id", app["id"])
    current = next((x for x in credentials if x["name"] == federation["name"]), None)
    if current:
        if any(current[k] != federation[k] for k in ("issuer", "subject", "audiences")):
            raise RuntimeError(f"Unexpected federated credential on {name}")
    else:
        az("ad", "app", "federated-credential", "create", "--id", app["id"],
           "--parameters", json.dumps(federation))
    call("gh", "variable", "set", f"TERRAFORM_{kind.upper()}_AZURE_CLIENT_ID",
         "--repo", REPO, "--body", app["appId"], capture=False)
    return app, sp


def copy_state(environment, container, key):
    az("storage", "container", "create", "--account-name", ACCOUNT, "--auth-mode", "login",
       "--name", container, "--public-access", "off")
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "source.json"
        destination = Path(directory) / "destination.json"
        az("storage", "blob", "download", "--account-name", ACCOUNT, "--auth-mode", "login",
           "--container-name", "tfstate", "--name", key, "--file", str(source),
           "--no-progress")
        document = json.loads(source.read_text())
        if document.get("version") != 4 or "resources" not in document:
            raise RuntimeError("Unexpected source state format; migration stopped")
        present = az("storage", "blob", "exists", "--account-name", ACCOUNT,
                     "--auth-mode", "login", "--container-name", container, "--name", key)
        if not present["exists"]:
            az("storage", "blob", "upload", "--account-name", ACCOUNT, "--auth-mode", "login",
               "--container-name", container, "--name", key, "--file", str(source),
               "--overwrite", "false")
        az("storage", "blob", "download", "--account-name", ACCOUNT, "--auth-mode", "login",
           "--container-name", container, "--name", key, "--file", str(destination),
           "--no-progress")
        if hashlib.sha256(source.read_bytes()).digest() != hashlib.sha256(destination.read_bytes()).digest():
            raise RuntimeError("Destination state differs; no overwrite was performed")
        print(f"{environment}: state copied and SHA-256 verified; {len(document['resources'])} resource records")


def prepare():
    running = gh_json(f"repos/{REPO}/actions/runs?per_page=100")["workflow_runs"]
    if any(x["status"] != "completed" and x["name"] in ("Terraform Deploy", "Terraform Destroy")
           for x in running):
        raise RuntimeError("Wait for deployment/destroy runs to finish before migrating state")
    account = az("account", "show")
    subscription = account["id"]
    scope = f"/subscriptions/{subscription}"
    repository = gh_json(f"repos/{REPO}")
    prefix = f"repo:{repository['owner']['login']}@{repository['owner']['id']}/{repository['name']}@{repository['id']}"
    principals = {}
    for kind, subject in (("plan", f"{prefix}:ref:refs/heads/main"),
                          ("test", f"{prefix}:environment:test"),
                          ("prod", f"{prefix}:environment:production")):
        _, sp = identity(kind, subject)
        principals[kind] = sp["id"]
    for groups in GROUPS.values():
        for group in groups:
            az("group", "create", "--name", group, "--location", "norwayeast",
               "--tags", "managed_by=terraform-bootstrap")
    role_name = "gtsdrt Terraform Plan Reader"
    roles = az("role", "definition", "list", "--name", role_name)
    expected = {"*/read", "Microsoft.Storage/storageAccounts/listKeys/action",
                "Microsoft.OperationalInsights/workspaces/sharedKeys/action"}
    if roles:
        actual = set(roles[0]["permissions"][0]["actions"])
        if actual != expected or roles[0]["permissions"][0].get("dataActions"):
            raise RuntimeError("Existing plan role has unexpected permissions")
    else:
        az("role", "definition", "create", "--role-definition", json.dumps({
            "Name": role_name, "IsCustom": True,
            "Description": "Read managed resources and computed diagnostic keys; no management writes",
            "Actions": sorted(expected), "NotActions": [], "DataActions": [],
            "NotDataActions": [], "AssignableScopes": [scope]}))
    for environment, groups in GROUPS.items():
        apply = principals["prod" if environment == "tf" else "test"]
        for group in groups:
            group_scope = f"{scope}/resourceGroups/{group}"
            grant(apply, "Contributor", group_scope)
            grant(principals["plan"], role_name, group_scope)
    storage = az("storage", "account", "show", "--name", ACCOUNT, "--resource-group", STATE_RG)
    operator = az("ad", "signed-in-user", "show")["id"]
    # Owner is a management role; it does not grant blob data-plane access.
    # This temporary grant is removed even if migration fails.
    existing = az("role", "assignment", "list", "--assignee", operator, "--scope", storage["id"])
    temporary_grant = None
    if not any(x["scope"].lower() == storage["id"].lower() and
               x["roleDefinitionName"] in ("Storage Blob Data Contributor", "Storage Blob Data Owner")
               for x in existing):
        temporary_grant = az("role", "assignment", "create", "--assignee-object-id", operator,
                             "--assignee-principal-type", "User", "--role",
                             "Storage Blob Data Contributor", "--scope", storage["id"],
                             "--name", str(uuid.uuid4()))
    try:
        for attempt in range(18):
            probe = subprocess.run(["az", "storage", "blob", "list", "--account-name", ACCOUNT,
                                    "--auth-mode", "login", "--container-name", "tfstate",
                                    "--only-show-errors", "-o", "none"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if probe.returncode == 0:
                break
            if attempt == 17:
                raise RuntimeError("Blob data role has not propagated; retry setup later")
            time.sleep(5)
        for environment, container, key, kind in (
            ("tf", "tfstate-prod", "executor.tfstate", "prod"),
            ("tf-test", "tfstate-test", "terraform-test.tfstate", "test")):
            copy_state(environment, container, key)
            container_scope = f"{storage['id']}/blobServices/default/containers/{container}"
            grant(principals["plan"], "Storage Blob Data Reader", container_scope)
            grant(principals[kind], "Storage Blob Data Contributor", container_scope)
    finally:
        if temporary_grant:
            az("role", "assignment", "delete", "--ids", temporary_grant["id"])
    for name, value in (("TERRAFORM_AZURE_SUBSCRIPTION_ID", subscription),
                        ("TERRAFORM_AZURE_TENANT_ID", account["tenantId"])):
        call("gh", "variable", "set", name, "--repo", REPO, "--body", value, capture=False)
    configure_plan_keys()
    print("Three OIDC identities, isolated state containers and environment plan keys configured")


def configure_plan_keys():
    key_directory = ROOT / ".github/plan-keys"
    key_directory.mkdir(exist_ok=True)
    for environment, github_environment in (("tf", "production"), ("tf-test", "test")):
        certificate = key_directory / f"{environment}.pem"
        if certificate.exists():
            names = call("gh", "secret", "list", "--repo", REPO, "--env", github_environment,
                         "--json", "name")
            if not any(x["name"] == "TF_PLAN_PRIVATE_KEY" for x in json.loads(names)):
                raise RuntimeError("Public certificate exists but environment private key is missing")
            continue
        with tempfile.TemporaryDirectory() as temporary:
            private = Path(temporary) / "private.pem"
            try:
                subprocess.run([os.getenv("OPENSSL_BIN", "openssl"), "req", "-x509", "-newkey",
                                "rsa:3072", "-nodes", "-days", "1825", "-keyout", str(private),
                                "-out", str(certificate), "-subj",
                                f"/CN={REPO.replace('/', '-')}-{environment}-tfplan"],
                               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                call("gh", "secret", "set", "TF_PLAN_PRIVATE_KEY", "--repo", REPO,
                     "--env", github_environment, data=private.read_text(), capture=False)
            except Exception:
                certificate.unlink(missing_ok=True)
                raise
    print("Environment plan keys configured; no private key is stored in the repository")


def retire():
    assignments = az("role", "assignment", "list", "--assignee", OLD_APP_ID, "--all")
    for assignment in assignments:
        az("role", "assignment", "delete", "--ids", assignment["id"])
    credentials = az("ad", "app", "credential", "list", "--id", OLD_APP_ID)
    for credential in credentials:
        az("ad", "app", "credential", "delete", "--id", OLD_APP_ID,
           "--key-id", credential["keyId"])
    names = {x["name"] for x in json.loads(call("gh", "secret", "list", "--repo", REPO,
                                               "--json", "name"))}
    for name in ("TERRAFORMEXECUTOR_AZURE_CLIENT_ID", "TERRAFORMEXECUTOR_AZURE_CLIENT_SECRET",
                 "TERRAFORMEXECUTOR_AZURE_SUBSCRIPTION_ID", "TERRAFORMEXECUTOR_AZURE_TENANT_ID",
                 "TERRAFORM_EXECUTOR_API_KEY"):
        if name in names:
            call("gh", "secret", "delete", name, "--repo", REPO, capture=False)
    az("storage", "account", "update", "--name", ACCOUNT, "--resource-group", STATE_RG,
       "--allow-shared-key-access", "false", "--allow-blob-public-access", "false")
    print("Old executor role grants and client secrets revoked; state Shared Key and anonymous blob access disabled")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("prepare", "keys", "retire"))
    args = parser.parse_args()
    os.umask(0o077)
    {"prepare": prepare, "keys": configure_plan_keys, "retire": retire}[args.stage]()
