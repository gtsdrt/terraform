"""Read-only drift plan. Publishes only the sanitized summary, never raw Terraform output."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plan_summary


def classify(exit_code):
    if exit_code == 0:
        return "clean"
    if exit_code == 2:
        return "drift"
    return "error"


def write_summary(path, text):
    if not path:
        return
    with open(path, "a", encoding="utf-8") as destination:
        destination.write(text)
        if not text.endswith("\n"):
            destination.write("\n")


def run(args):
    plan = Path(args.plan)
    log = Path(args.log)
    descriptor = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as destination:
            result = subprocess.run(
                ["terraform", "-chdir", args.directory, "plan", "-lock=false",
                 "-input=false", "-no-color", "-detailed-exitcode",
                 f"-var-file={args.var_file}", f"-out={plan}"],
                stdout=destination, stderr=subprocess.STDOUT)
        state = classify(result.returncode)
        if state == "clean":
            print("No drift detected.")
            return 0
        if state == "drift":
            shown = subprocess.run(
                ["terraform", "-chdir", args.directory, "show", "-json", str(plan)],
                check=True, stdout=subprocess.PIPE)
            write_summary(args.summary, plan_summary.summary(json.loads(shown.stdout)))
            print("::warning::Drift detected. Review the sanitized resource actions above; raw output was withheld.")
            return 1
        print("::error::Drift plan failed. Raw output was withheld; reproduce with the read-only identity.")
        return 1
    finally:
        plan.unlink(missing_ok=True)
        log.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--var-file", default="terraform.tfvars")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--summary", default="")
    return run(parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())
