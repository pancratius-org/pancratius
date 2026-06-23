"""PAN024 — primary library targets must not be argparse flags.

Typed resource selectors such as ``book:50`` and ``poem:1`` are positional when
they name the command's primary library target. This checker guards the retired
``--book`` / ``--poem`` / ``--number`` / ``--into`` target-flag shape without
blocking option flags such as ``--books-root``.
"""

from __future__ import annotations

import ast
import os
import sys
from dataclasses import dataclass
from pathlib import Path

FORBIDDEN_TARGET_FLAGS: frozenset[str] = frozenset({"--book", "--poem", "--number", "--into"})


def _audit_root() -> Path:
    env = os.environ.get("PANCRATIUS_AUDIT_ROOT")
    return Path(env).resolve() if env else Path(__file__).resolve().parents[2]


ROOT = _audit_root()
CLI_DOOR = ROOT / "pancratius" / "cli.py"


@dataclass(frozen=True, slots=True)
class TargetFlag:
    flag: str
    lineno: int


def _target_flags(tree: ast.Module) -> list[TargetFlag]:
    findings: list[TargetFlag] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "add_argument"):
            continue
        for arg in node.args:
            if (
                isinstance(arg, ast.Constant)
                and isinstance(arg.value, str)
                and arg.value in FORBIDDEN_TARGET_FLAGS
            ):
                findings.append(TargetFlag(arg.value, node.lineno))
    return findings


def main() -> int:
    if not CLI_DOOR.exists():
        print(f"PASS: no CLI door at {CLI_DOOR} to scan")
        return 0

    tree = ast.parse(CLI_DOOR.read_text(encoding="utf-8"))
    failures = _target_flags(tree)
    if failures:
        print("FAIL: CLI primary-target flags violated", file=sys.stderr)
        for failure in failures:
            print(
                "  "
                f"pancratius/cli.py:{failure.lineno}: {failure.flag} is a primary "
                "resource target; use a typed positional selector such as book:50 or poem:1.",
                file=sys.stderr,
            )
        return 1

    print("PASS: CLI uses positional typed selectors instead of retired target flags")
    return 0


if __name__ == "__main__":
    sys.exit(main())
