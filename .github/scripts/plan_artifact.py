"""Encrypt exact Terraform plans; only apply jobs receive the private key."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile


def context(environment):
    return {
        "version": 1,
        "environment": environment,
        **{name: os.environ[name] for name in
           ("GITHUB_REPOSITORY", "GITHUB_SHA", "GITHUB_RUN_ID")},
    }


def seal(plan, certificate, output, environment):
    payload = Path(plan).read_bytes()
    document = {"context": context(environment),
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


def unseal(encrypted, certificate, output, environment):
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
        if document["context"] != context(environment):
            raise ValueError("Plan does not belong to this repository, commit, run and environment")
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
    args = parser.parse_args()
    try:
        (seal if args.operation == "seal" else unseal)(
            args.input, args.certificate, args.output, args.environment)
    except (ValueError, KeyError, subprocess.CalledProcessError, OSError):
        # Do not print subprocess stdout, the decrypted document or secret values.
        parser.exit(1, "Plan encryption/decryption failed; check keys and run context.\n")


if __name__ == "__main__":
    main()
