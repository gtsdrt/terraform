"""Validate workflows, adapting GitHub's queue:max syntax for actionlint 1.7.12.

GitHub supports this key, but the pinned actionlint version doesn't yet:
https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency
Only the exact top-level concurrency queue:max setting is replaced by a comment.
All other source lines (and line numbers) are preserved for actionlint.
"""
from pathlib import Path
import subprocess
import sys
import tempfile


def lint_source(source):
    block = []
    capturing = False
    for line in source.splitlines():
        if line.strip() and not line.startswith((" ", "#")):
            capturing = line.strip() == "concurrency:"
        elif capturing and line.strip() and not line.lstrip().startswith("#"):
            block.append(line.strip())
    if "queue: max" in block:
        if block.count("queue: max") != 1 or "cancel-in-progress: false" not in block:
            raise ValueError("queue:max requires one queue key and literal cancel-in-progress:false")
    concurrency = False
    lines = []
    for line in source.splitlines(keepends=True):
        if line.strip() and not line.startswith((" ", "#")):
            concurrency = line.strip() == "concurrency:"
        if concurrency and line.rstrip() == "  queue: max":
            line = "  # queue: max (validated GitHub syntax; unsupported by actionlint 1.7.12)\n"
        lines.append(line)
    return "".join(lines)


def main():
    root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory() as temporary:
        paths = []
        for source in sorted((root / ".github/workflows").glob("*.yml")):
            path = Path(temporary) / source.name
            path.write_text(lint_source(source.read_text()))
            paths.append(str(path))
        return subprocess.run([sys.argv[1], "-pyflakes=", *paths], cwd=root).returncode


if __name__ == "__main__":
    sys.exit(main())
