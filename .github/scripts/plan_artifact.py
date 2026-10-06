"""Encrypt exact Terraform plans; only apply jobs receive the private key."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from datetime import datetime, timezone

PLAN_LIFETIME_SECONDS = 24 * 60 * 60


class ExpiredPlanError(ValueError):
    pass


def context(environment, purpose="deploy", scope="all"):
    return {
        "version": 2,
        "environment": environment,
        "purpose": purpose,
        "scope": scope,
        **{name: os.environ[name] for name in
           ("GITHUB_REPOSITORY", "GITHUB_SHA", "GITHUB_RUN_ID")},
    }


def seal(plan, certificate, output, environment, purpose="deploy", scope="all"):
    payload = Path(plan).read_bytes()
    created = int(time.time())
    document = {"context": context(environment, purpose, scope),
                "created_at": created, "expires_at": created + PLAN_LIFETIME_SECONDS,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "plan": base64.b64encode(payload).decode("ascii")}
    with tempfile.TemporaryDirectory() as temporary:
        source = Path(temporary) / "payload.json"
        source.write_text(json.dumps(document))
        source.chmod(0o600)
        subprocess.run([os.getenv("OPENSSL_BIN", "openssl"), "cms", "-encrypt",
                        "-binary", "-aes-256-gcm", "-in", str(source),
                        "-outform", "DER", "-out", str(output),
                        "-recip", str(certificate), "-keyopt", "rsa_padding_mode:oaep",
                        "-keyopt", "rsa_oaep_md:sha256"], check=True)
    return document["expires_at"]


def unseal(encrypted, certificate, output, environment, purpose="deploy", scope="all"):
    private_key = os.environ.get("TF_PLAN_PRIVATE_KEY", "")
    if not private_key:
        raise ValueError("The environment TF_PLAN_PRIVATE_KEY secret is missing")
    with tempfile.TemporaryDirectory() as temporary:
        key = Path(temporary) / "private.pem"
        key.write_text(private_key)
        key.chmod(0o600)
        result = subprocess.run(
            [os.getenv("OPENSSL_BIN", "openssl"), "cms", "-decrypt", "-binary",
             "-inform", "DER", "-in", str(encrypted), "-recip", str(certificate),
             "-inkey", str(key)], check=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)
        document = json.loads(result.stdout)
        if document["context"] != context(environment, purpose, scope):
            raise ValueError("Plan does not belong to this repository, commit, run and environment")
        created, expires = document["created_at"], document["expires_at"]
        if type(created) is not int or type(expires) is not int or expires - created != PLAN_LIFETIME_SECONDS:
            raise ValueError("Invalid plan validity interval")
        now = int(time.time())
        if created > now + 60:
            raise ValueError("Plan timestamp is in the future")
        if now >= expires:
            raise ExpiredPlanError("Plan expired")
        payload = base64.b64decode(document["plan"], validate=True)
        if hashlib.sha256(payload).hexdigest() != document["sha256"]:
            raise ValueError("Plan checksum does not match")
        # Create only after authentication and context verification succeed.
        with open(output, "xb") as destination:
            os.chmod(output, 0o600)
            destination.write(payload)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("seal", "unseal"))
    parser.add_argument("--input", required=True)
    parser.add_argument("--certificate", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--environment", choices=("tf", "tf-test"), required=True)
    parser.add_argument("--purpose", choices=("deploy", "destroy"), default="deploy")
    parser.add_argument("--scope", choices=("all", "network", "azlandingzone"), default="all")
    args = parser.parse_args()
    try:
        expires = (seal if args.operation == "seal" else unseal)(
            args.input, args.certificate, args.output, args.environment, args.purpose, args.scope)
        if args.operation == "seal":
            timestamp = datetime.fromtimestamp(expires, timezone.utc).isoformat()
            print(f"Plan valid until {timestamp}. After expiry, start a new workflow and review its new plan.")
    except ExpiredPlanError:
        parser.exit(1, "Plan expired. Start a new workflow and review its new plan; do not retry this apply job.\n")
    except (ValueError, KeyError, subprocess.CalledProcessError, OSError):
        # Do not print subprocess stdout, the decrypted document or secret values.
        parser.exit(1, "Plan encryption/decryption failed; check keys and run context.\n")


if __name__ == "__main__":
    main()
