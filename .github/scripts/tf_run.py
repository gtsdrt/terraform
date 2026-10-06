"""Run Terraform without publishing raw output; report fixed error categories only."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ERRORS = {
    "authorization": ("authorizationfailed", "authorizationpermissionmismatch", "forbidden", "aadsts"),
    "state-lock": ("error acquiring the state lock", "leasealreadypresent", "leaseidmissing"),
    "stale-plan": ("saved plan is stale", "saved plan does not match"),
    "quota": ("quotaexceeded", "operationnotallowed", "subscriptionisoverquotaforsku"),
    "name-conflict": ("alreadyexists", "already exists", "already in use", "conflict"),
    "network": ("context deadline exceeded", "no such host", "connection refused", "i/o timeout"),
    "registration": ("missingsubscriptionregistration", "noregisteredproviderfound"),
}


def categories(output):
    text = output.lower()
    return [name for name, tokens in ERRORS.items() if any(token in text for token in tokens)] or ["unclassified"]


def run(command, log):
    path = Path(log)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.chmod(path, 0o600)
        with os.fdopen(descriptor, "wb") as destination:
            result = subprocess.run(command, stdout=destination, stderr=subprocess.STDOUT)
        if result.returncode:
            labels = ", ".join(categories(path.read_text(errors="replace")))
            print(f"::error::Terraform command failed ({labels}). Raw output was withheld; reproduce with an authorized identity.")
        else:
            print("Terraform command completed.")
        return result.returncode
    finally:
        path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("A command is required")
    try:
        return run(command, args.log)
    except OSError:
        print("::error::Unable to execute Terraform command. Raw diagnostics were withheld.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
