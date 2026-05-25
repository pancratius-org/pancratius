#!/usr/bin/env -S uv run --quiet
"""PAN019 — CLI door verify-boundary guard (mutate/verify cut, PAN015 family).

The two-doors split (docs/tooling.md) cuts on what a command does to the world:
the `pancratius` console-script MUTATES the corpus; verification (`check`, `test`,
`audit`) is PURE and lives behind the `npm` site door. So the `pancratius` door
must expose NO verification verb — concretely, no `audit` group/verb and no `site`
proxy group (the two rejected alternatives tooling.md names). A `pancratius site
audit → npm run audit` proxy would invert the doc's cut at the grammar level.

This audit asserts that `pancratius/cli.py` registers no argparse sub-parser named
`audit` or `site` (at ANY nesting level), so the boundary can't silently drift in.
It derives the forbidden set from the doc's named rejected alternatives, not from a
guess — the door grows new MUTATE verbs freely; only the verify names are barred.

Honours ``PANCRATIUS_AUDIT_ROOT`` (the harness points it at a fixture); wrapped as
PAN019 in scripts/audit/rules/imports.ts.
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

# The verify-side names that must never become a door verb/group (the mutate/verify
# cut). Derived from tooling.md's rejected alternatives ("a `site` proxy group",
# "a `pancratius site audit` verb"): verification belongs to the npm site door.
FORBIDDEN_VERBS: frozenset[str] = frozenset({"audit", "site"})


def _audit_root() -> Path:
    """The tree to scan: the fixture root when ``PANCRATIUS_AUDIT_ROOT`` is set,
    else the repo root (python -> audit -> scripts -> root)."""
    env = os.environ.get("PANCRATIUS_AUDIT_ROOT")
    return Path(env).resolve() if env else Path(__file__).resolve().parents[3]


ROOT = _audit_root()
CLI_DOOR = ROOT / "pancratius" / "cli.py"


def _registered_subparser_names(tree: ast.Module) -> list[tuple[str, int]]:
    """Every `<x>.add_parser("<name>", …)` name registered in the module, with its
    line — these are the door's groups/nouns/verbs."""
    names: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "add_parser"):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            names.append((first.value, node.lineno))
    return names


def main() -> int:
    if not CLI_DOOR.exists():
        # No door to check (e.g. a fixture omitting it); the door's existence is not
        # this rule's concern. Treat as clean.
        print(f"PASS: no CLI door at {CLI_DOOR} to scan")
        return 0

    tree = ast.parse(CLI_DOOR.read_text(encoding="utf-8"))
    failures = [
        f"pancratius/cli.py:{lineno}: the CLI door registers a `{name}` sub-parser — "
        f"verification ({', '.join(sorted(FORBIDDEN_VERBS))}) is the npm site door's "
        "job (the mutate/verify cut), never a `pancratius` verb."
        for name, lineno in _registered_subparser_names(tree)
        if name in FORBIDDEN_VERBS
    ]

    if failures:
        print("FAIL: CLI door verify-boundary violated", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 1

    print(f"PASS: the CLI door exposes no verify verb ({', '.join(sorted(FORBIDDEN_VERBS))})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
