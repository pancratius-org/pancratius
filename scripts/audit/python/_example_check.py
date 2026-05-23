#!/usr/bin/env -S uv run --quiet
"""SCAFFOLD PROOF — Python-subprocess path. Not a real contract.

Exits non-zero iff the marker file ``AUDIT_PROOF_PY_BAD`` exists anywhere under
the audit root. Proves the harness's Python normalizer and, crucially, the
``PANCRATIUS_AUDIT_ROOT`` override that lets a wrapped check run against a tiny
fixture instead of the repo. Delete with the TS proof rule once PAN002 lands.

The root-resolution + pruned-walk shape here is the template every folded Python
check will follow so it stays fixture-testable.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

MARKER = "AUDIT_PROOF_PY_BAD"

# Directory pruning mirrors the TS walker (scripts/audit/lib/repo.ts) exactly:
# skip these non-dot build/vendor/report trees, plus every dot-directory except
# the few a rule legitimately scans (.github). New tool caches (.venv,
# .ruff_cache, …) are dot-dirs and so are pruned automatically.
_IGNORE_DIRS: frozenset[str] = frozenset(
    {"node_modules", "dist", "__pycache__", "playwright-report", "test-results", "coverage"}
)
_KEEP_DOT_DIRS: frozenset[str] = frozenset({".github"})


def _skip_dir(name: str) -> bool:
    if name in _IGNORE_DIRS:
        return True
    return name.startswith(".") and name not in _KEEP_DOT_DIRS


def audit_root() -> Path:
    """The tree to scan: the fixture root when set, else the repo root."""
    env = os.environ.get("PANCRATIUS_AUDIT_ROOT")
    if env:
        return Path(env).resolve()
    # scripts/audit/python/_example_check.py -> repo root is four levels up.
    return Path(__file__).resolve().parents[3]


# The harness's own fixtures tree holds known-bad content on purpose; a real-repo
# scan must skip it (mirrors HARNESS_FIXTURES_ABS in scripts/audit/lib/repo.ts).
_FIXTURES_REL = Path("scripts/audit/fixtures")


def find_marker(root: Path) -> list[Path]:
    fixtures = (root / _FIXTURES_REL).resolve()
    hits: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not _skip_dir(d)]
        if Path(dirpath).resolve() == fixtures:
            dirnames[:] = []
            continue
        if MARKER in filenames:
            hits.append(Path(dirpath) / MARKER)
    return hits


def main() -> int:
    root = audit_root()
    hits = find_marker(root)
    if hits:
        print(f"FAIL: scaffold marker {MARKER} present", file=sys.stderr)
        for path in hits:
            print(f"  {path}", file=sys.stderr)
        return 1
    print(f"PASS: no {MARKER} marker under {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
