"""Check the public checkout before CI builds distributable images."""
from fnmatch import fnmatchcase
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = (
    "companion/gateway.py", "companion/hosted*.py", "companion/routes/hosted*.py",
    "companion/test_gateway*.py", "companion/test_hosted*.py",
    "setup/terraform/hosted-gcp/*", "scripts/*hosted*",
    ".github/workflows/hosted-checks.yml", "operator/*",
    "BATCH.md", "DECISIONS.md", "MAP.yaml", "UI-PLACEMENT.yaml",
    "docs/RELEASING.md", "docs/distribution-boundary.md", "docs/*-DRAFT.md",
    "export-allowlist.txt", "scripts/export-oss.sh", "scripts/test_export_oss.py",
)


def main() -> int:
    paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    leaked = sorted(p for p in paths if p and any(fnmatchcase(p, pattern) for pattern in PRIVATE))
    if leaked:
        print("Private files in the public checkout:\n" + "\n".join(leaked), file=sys.stderr)
        return 1
    print("Public source boundary passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
