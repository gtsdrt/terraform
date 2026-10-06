"""Prepare isolated v2 backend files and foundation inputs; never reset old state.

Requires an Azure administrator with roleAssignments/write and access to the
existing state account, plus read access to GitHub repository variables.
Resource groups and workload RBAC are subsequently created by tf-foundation.
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bootstrap_security as bootstrap

STATE_FILES = (
    ('tfstate-foundation', 'foundation-v2.tfstate'),
    ('tfstate-prod', 'executor-v2.tfstate'),
    ('tfstate-test', 'terraform-test-v2.tfstate'),
)
KINDS = ('plan', 'test', 'prod')


def guid(value):
    return str(uuid.UUID(value))


def configuration(variables, account, principals):
    subscription = guid(variables['TERRAFORM_AZURE_SUBSCRIPTION_ID'])
    tenant = guid(variables['TERRAFORM_AZURE_TENANT_ID'])
    if guid(account['id']) != subscription or guid(account['tenantId']) != tenant:
        raise ValueError('Select the subscription and tenant configured in GitHub first.')
    clients = [guid(variables[f'TERRAFORM_{kind.upper()}_AZURE_CLIENT_ID']) for kind in KINDS]
    if len(set(clients)) != 3:
        raise ValueError('The three configured Client IDs must be distinct.')
    objects = {}
    for kind, client in zip(KINDS, clients):
        service = principals[kind]
        if guid(service['appId']) != client or service['displayName'] != f'gtsdrt-terraform-{kind}':
            raise ValueError('Unexpected service principal; do not grant roles to another application.')
        objects[kind] = guid(service['id'])
    if len(set(objects.values())) != 3:
        raise ValueError('The three service principal Object IDs must be distinct.')
    return {'subscription_id': subscription, 'tenant_id': tenant, 'principal_ids': objects}


def empty_state():
    return {'version': 4, 'terraform_version': '1.9.8', 'serial': 0,
            'lineage': str(uuid.uuid4()), 'outputs': {}, 'resources': [], 'check_results': None}


def seed_state(container, key):
    if (container, key) not in STATE_FILES:
        raise ValueError('Only the three reviewed v2 state keys can be initialized.')
    args = ('--account-name', bootstrap.ACCOUNT, '--auth-mode', 'login', '--container-name', container)
    present = bootstrap.az('storage', 'blob', 'exists', *args, '--name', key)
    if present['exists']:
        print(f'{container}/{key}: already exists; left unchanged')
        return
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'empty.tfstate'
        path.write_text(json.dumps(empty_state()))
        path.chmod(0o600)
        bootstrap.az('storage', 'blob', 'upload', *args, '--name', key, '--file', str(path),
                     '--overwrite', 'false', '--no-progress')
    print(f'{container}/{key}: initialized without overwriting existing blobs')


def assignments(principal, scope):
    return bootstrap.az('role', 'assignment', 'list', '--assignee', principal, '--scope', scope)


def exact_role(items, scope, role):
    return any(item['scope'].lower() == scope.lower() and item['roleDefinitionName'] == role
               and not item.get('condition') for item in items)


def ensure_role(principal, role, scope, principal_type='ServicePrincipal'):
    if not exact_role(assignments(principal, scope), scope, role):
        bootstrap.az('role', 'assignment', 'create', '--assignee-object-id', principal,
                     '--assignee-principal-type', principal_type, '--role', role, '--scope', scope)


@contextmanager
def temporary_blob_access(operator, scope):
    temporary = None
    existing = assignments(operator, scope)
    if not any(exact_role(existing, scope, role) for role in ('Storage Blob Data Contributor', 'Storage Blob Data Owner')):
        temporary = bootstrap.az('role', 'assignment', 'create', '--assignee-object-id', operator,
                                 '--assignee-principal-type', 'User', '--role', 'Storage Blob Data Contributor',
                                 '--scope', scope, '--name', str(uuid.uuid4()))
    try:
        for attempt in range(120):
            probe = subprocess.run(['az', 'storage', 'container', 'list', '--account-name', bootstrap.ACCOUNT,
                                    '--auth-mode', 'login', '--only-show-errors', '-o', 'none'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if probe.returncode == 0:
                break
            if attempt == 119:
                raise RuntimeError('Blob role has not propagated. Temporary grant will be removed; retry setup later.')
            time.sleep(5)
        yield
    finally:
        if temporary:
            bootstrap.az('role', 'assignment', 'delete', '--ids', temporary['id'])


def prepare(apply=False):
    variables = {item['name']: item['value'] for item in json.loads(bootstrap.call(
        'gh', 'variable', 'list', '--repo', bootstrap.REPO, '--json', 'name,value'))}
    account = bootstrap.az('account', 'show')
    principals = {kind: bootstrap.az('ad', 'sp', 'show', '--id', variables[f'TERRAFORM_{kind.upper()}_AZURE_CLIENT_ID']) for kind in KINDS}
    config = configuration(variables, account, principals)
    state_account = bootstrap.az('storage', 'account', 'show', '--name', bootstrap.ACCOUNT,
                                 '--resource-group', bootstrap.STATE_RG)
    path = bootstrap.ROOT / 'tf-foundation/foundation.tfvars.local.json'
    # Inputs contain metadata only; protected locally and excluded by .gitignore.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w') as destination:
        os.chmod(path, 0o600)
        json.dump(config, destination, indent=2)
    print('Validated distinct identities; wrote tf-foundation/foundation.tfvars.local.json')
    if not apply:
        print('Preview only: no Azure writes. --apply initializes absent v2 blobs and backend container roles.')
        for container, key in STATE_FILES:
            print(f'Prepare {container}/{key}; existing blobs are retained.')
        return
    runs = bootstrap.gh_json(f'repos/{bootstrap.REPO}/actions/runs?per_page=100')['workflow_runs']
    writers = ('Terraform Deploy', 'Terraform Destroy', 'Terraform Test Recovery')
    if any(run['name'] in writers and run['status'] != 'completed' for run in runs):
        raise RuntimeError('Stop deployment, destroy and recovery runs before initializing v2 backends.')
    operator = bootstrap.az('ad', 'signed-in-user', 'show')['id']
    with temporary_blob_access(operator, state_account['id']):
        for container, key in STATE_FILES:
            bootstrap.az('storage', 'container', 'create', '--account-name', bootstrap.ACCOUNT,
                         '--auth-mode', 'login', '--name', container, '--public-access', 'off')
            scope = f"{state_account['id']}/blobServices/default/containers/{container}"
            if container == 'tfstate-foundation':
                ensure_role(operator, 'Storage Blob Data Contributor', scope, 'User')
            else:
                kind = 'prod' if container == 'tfstate-prod' else 'test'
                ensure_role(config['principal_ids'][kind], 'Storage Blob Data Contributor', scope)
                ensure_role(config['principal_ids']['plan'], 'Storage Blob Data Reader', scope)
            seed_state(container, key)
    print('v2 backends ready. Next: administrator terraform init/plan/apply in tf-foundation.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        prepare(args.apply)
    except (ValueError, KeyError, RuntimeError) as error:
        parser.exit(1, f'{error}\n')
