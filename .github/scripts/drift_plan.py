"""Read-only drift plan. Publishes only the sanitized summary, never raw Terraform output."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plan_summary
import tf_run


def classify(exit_code):
    if exit_code == 0:
        return "clean"
    if exit_code == 2:
        return "drift"
    return "error"


def plan_command(directory, var_file, plan):
    # Terraform's global option is -chdir=DIR; a space-separated -chdir DIR is invalid.
    return ["terraform", f"-chdir={directory}", "plan", "-lock=false", "-input=false",
            "-no-color", "-detailed-exitcode", f"-var-file={var_file}", f"-out={plan}"]


def show_command(directory, plan):
    return ["terraform", f"-chdir={directory}", "show", "-json", str(plan)]


def state_resources(module):
    """Count *managed* resources in a prior_state root module, including nested child modules.

    Data sources (mode "data") survive a full destroy: they are re-read on the
    next plan and stay in prior_state. Counting them makes a torn-down
    environment look partially deployed, so the run is misclassified as drift.
    """
    if not isinstance(module, dict):
        return 0
    managed = [r for r in module.get("resources") or []
               if isinstance(r, dict) and r.get("mode", "managed") != "data"]
    return len(managed) + sum(
        state_resources(child) for child in module.get("child_modules") or [])


def planned_changes(document):
    """Resource changes that are neither no-op nor a data-source read."""
    return [item for item in document.get("resource_changes", [])
            if item.get("change", {}).get("actions") not in (["no-op"], ["read"])]


def not_deployed(document):
    """The environment is torn down: empty prior state and nothing but creates planned.

    Counts as "not deployed" rather than drift, so a deliberately destroyed
    environment does not make the scheduled run fail every day. A partially
    deployed state (some resources present) still counts as drift.
    """
    prior = document.get("prior_state") or {}
    if state_resources((prior.get("values") or {}).get("root_module")):
        return False
    changes = planned_changes(document)
    return bool(changes) and all(item["change"]["actions"] == ["create"] for item in changes)


def write_summary(path, text):
    if not path:
        return
    with open(path, "a", encoding="utf-8") as destination:
        destination.write(text)
        if not text.endswith("\n"):
            destination.write("\n")


def publish(document, summary_path):
    """Emit the sanitized report to the log *and* the run Summary.

    The Summary tab is invisible from `gh run view --log`, so a log that only says
    "review the actions above" left operators with nothing to review.
    """
    text = plan_summary.summary(document)
    write_summary(summary_path, text)
    print(text)
    return text


def run(args):
    plan = Path(args.plan)
    log = Path(args.log)
    descriptor = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as destination:
            result = subprocess.run(plan_command(args.directory, args.var_file, plan),
                                    stdout=destination, stderr=subprocess.STDOUT)
        state = classify(result.returncode)
        if state == "clean":
            print("No drift detected.")
            return 0
        if state == "drift":
            shown = subprocess.run(show_command(args.directory, plan),
                                   check=True, stdout=subprocess.PIPE)
            document = json.loads(shown.stdout)
            publish(document, args.summary)
            if not_deployed(document):
                print("::notice::Nothing is deployed (empty state). The create plan above is "
                      "informational, not drift; the run stays green.")
                return 0
            print("::warning::Drift detected. Review the sanitized resource actions above; raw output was withheld.")
            return 1
        labels = ", ".join(tf_run.categories(log.read_text(errors="replace")))
        print(f"::error::Drift plan failed ({labels}). Raw output was withheld; reproduce with the read-only identity.")
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
