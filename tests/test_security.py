import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / ".github/scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


artifact = load("plan_artifact")
summary = load("plan_summary")


class SecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temp.name)
        cls.key = cls.directory / "private.pem"
        cls.cert = cls.directory / "public.pem"
        subprocess.run([os.getenv("OPENSSL_BIN", "openssl"), "req", "-x509", "-newkey",
                        "rsa:2048", "-nodes", "-keyout", str(cls.key), "-out", str(cls.cert),
                        "-days", "1", "-subj", "/CN=security-test"], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.context = patch.dict(os.environ, {
            "GITHUB_REPOSITORY": "test/terraform", "GITHUB_SHA": "a" * 40,
            "GITHUB_RUN_ID": "42", "TF_PLAN_PRIVATE_KEY": self.key.read_text()})
        self.context.start()
        self.addCleanup(self.context.stop)
        self.case = tempfile.TemporaryDirectory()
        self.addCleanup(self.case.cleanup)
        self.plan = Path(self.case.name) / "tfplan"
        self.encrypted = Path(self.case.name) / "plan.cms"
        self.output = Path(self.case.name) / "decrypted"
        self.plan.write_bytes(b"sensitive-test-plan\x00\xff")
        artifact.seal(self.plan, self.cert, self.encrypted, "tf")

    def test_exact_plan_round_trip(self):
        artifact.unseal(self.encrypted, self.cert, self.output, "tf")
        self.assertEqual(self.output.read_bytes(), self.plan.read_bytes())
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(self.plan.read_bytes(), self.encrypted.read_bytes())

    def test_replay_from_other_run_commit_repository_or_environment_is_rejected(self):
        for name in ("GITHUB_REPOSITORY", "GITHUB_SHA", "GITHUB_RUN_ID"):
            with self.subTest(name=name), patch.dict(os.environ, {name: "different"}):
                with self.assertRaises(ValueError):
                    artifact.unseal(self.encrypted, self.cert, self.output, "tf")
                self.assertFalse(self.output.exists())
        with self.assertRaises(ValueError):
            artifact.unseal(self.encrypted, self.cert, self.output, "tf-test")

    def test_modified_ciphertext_is_rejected_without_writing_plaintext(self):
        ciphertext = bytearray(self.encrypted.read_bytes())
        ciphertext[-1] ^= 1
        self.encrypted.write_bytes(ciphertext)
        with self.assertRaises(subprocess.CalledProcessError):
            artifact.unseal(self.encrypted, self.cert, self.output, "tf")
        self.assertFalse(self.output.exists())

    def test_missing_private_key_is_rejected(self):
        with patch.dict(os.environ, {"TF_PLAN_PRIVATE_KEY": ""}):
            with self.assertRaises(ValueError):
                artifact.unseal(self.encrypted, self.cert, self.output, "tf")

    def test_summary_omits_secrets_even_if_not_marked_sensitive(self):
        document = {"variables": {"password": {"value": "secret-marker"}},
                    "output_changes": {"token": {"after": "secret-marker"}},
                    "resource_changes": [{"address": "example.this", "change": {
                        "actions": ["update"], "before": {"key": "secret-marker"},
                        "after": {"key": "secret-marker"}, "after_sensitive": {}}}]}
        rendered = summary.summary(document)
        self.assertNotIn("secret-marker", rendered)
        self.assertIn("example.this", rendered)

    def test_confirmation_is_literal_and_never_evaluates_shell(self):
        script = ROOT / ".github/scripts/check_destroy_inputs.sh"
        marker = Path(self.case.name) / "injected"
        env = {**os.environ, "DESTROY_CONFIRM": f"DESTROY$(touch {marker})",
               "DESTROY_ENV": "tf", "DESTROY_SCOPE": "all"}
        self.assertNotEqual(subprocess.run(["bash", str(script)], env=env,
                                           capture_output=True).returncode, 0)
        self.assertFalse(marker.exists())
        env["DESTROY_CONFIRM"] = "DESTROY"
        self.assertEqual(subprocess.run(["bash", str(script)], env=env,
                                       capture_output=True).returncode, 0)
        env["DESTROY_ENV"] = "../../other"
        self.assertNotEqual(subprocess.run(["bash", str(script)], env=env,
                                           capture_output=True).returncode, 0)


if __name__ == "__main__":
    unittest.main()
