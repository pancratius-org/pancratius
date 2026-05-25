#!/usr/bin/env -S uv run --quiet
"""PAN017 — import work-kinds guard (retired-capability surface, PAN015 family).

The import CLI (``scripts/import_docx.py``) converts corpus WORKS only. "Work
kinds" has a single source of truth: ``WORK_KINDS`` in ``scripts/lib/kinds.py``.
This audit asserts the import CLI derives its ``--kind`` choices from that SoT
rather than hardcoding a (possibly project-including) list, and that the SoT
itself stays coherent with the routing map:

  1. ``import_docx.py``'s ``--kind`` argparse ``choices`` == ``WORK_KINDS``;
  2. ``"project" not in WORK_KINDS`` — projects are themed sections, not works,
     so they must never be an importable/convertible kind (PAN004 / PAN015);
  3. ``WORK_KINDS`` is a subset of ``SEGMENT_OF`` keys — every work kind still
     routes (``SEGMENT_OF`` deliberately also carries ``project`` for routing);
  4. the ``pancratius`` CLI door (``pancratius/cli.py``) must NOT redeclare
     ``--kind`` with choices that drift from ``WORK_KINDS``. The door is meant to
     DEFER (declare no ``--kind`` of its own — it reuses
     ``import_docx.add_import_arguments`` so the boundary is owned in one place);
     if it ever declares one, it must be ``WORK_KINDS``-derived. Either way the
     ``book|poem`` boundary stays audit-enforced on the CLI surface, not just the
     standalone importer.

It derives, it does not restate (PAN003): the kinds come from ``kinds.py`` and
the choices come from the CLI's own argparse, so re-adding ``project`` to the
import surface trips the rule because the two disagree — not because the rule
hardcodes a banned value.

Honours ``PANCRATIUS_AUDIT_ROOT`` (the harness points it at a fixture);
wrapped as PAN017 in scripts/audit/rules/imports.ts.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import sys
from pathlib import Path


def _audit_root() -> Path:
    """The tree to scan: the fixture root when ``PANCRATIUS_AUDIT_ROOT`` is set,
    else the repo root. From scripts/audit/python/import_work_kinds.py the repo
    root is four levels up (python -> audit -> scripts -> root)."""
    env = os.environ.get("PANCRATIUS_AUDIT_ROOT")
    return Path(env).resolve() if env else Path(__file__).resolve().parents[3]


ROOT = _audit_root()
PY_KINDS = ROOT / "scripts" / "lib" / "kinds.py"
IMPORT_CLI = ROOT / "scripts" / "import_docx.py"
# The `pancratius` library door. Optional in a fixture tree (a fixture may omit it);
# when present it must defer or derive --kind (rule 4).
CLI_DOOR = ROOT / "pancratius" / "cli.py"

# The argparse option whose `choices` must equal WORK_KINDS, and the SoT name the
# CLI is expected to reference for those choices.
KIND_OPTION = "--kind"
SOT_NAME = "WORK_KINDS"


def _load_kinds() -> tuple[tuple[str, ...], dict[str, str]]:
    """Return (WORK_KINDS, SEGMENT_OF) from scripts/lib/kinds.py."""
    spec = importlib.util.spec_from_file_location("pancratius_kinds", PY_KINDS)
    if spec is None or spec.loader is None:
        raise ValueError(f"could not load {PY_KINDS}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return tuple(module.WORK_KINDS), dict(module.SEGMENT_OF)


def _imports_sot_from_kinds(tree: ast.Module) -> bool:
    """True if the module does `from lib.kinds import … WORK_KINDS …`."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("lib.kinds"):
            if any(alias.name == SOT_NAME for alias in node.names):
                return True
    return False


def _kind_choices(tree: ast.Module) -> tuple[str, list[str] | None, str | None, int | None]:
    """Find the `add_argument("--kind", … choices=… )` call and describe its
    `choices` value. Returns (form, literal_members, name_id, lineno):

    - form == "name":    choices is a bare Name, or `list(Name)` / `tuple(Name)`
                         (e.g. WORK_KINDS); name_id is that Name's identifier.
    - form == "literal": choices is a list/tuple of string literals; members set.
    - form == "missing": no `--kind` add_argument, or no `choices=` kwarg.
    - form == "other":   choices is some other expression we won't resolve.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "add_argument"):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and first.value == KIND_OPTION):
            continue
        choices_kw = next((kw for kw in node.keywords if kw.arg == "choices"), None)
        if choices_kw is None:
            return ("missing", None, None, node.lineno)
        value = choices_kw.value
        # Bare name: `choices=WORK_KINDS`.
        if isinstance(value, ast.Name):
            return ("name", None, value.id, value.lineno)
        # Wrapped name: `choices=list(WORK_KINDS)` / `tuple(WORK_KINDS)` — a
        # legitimate SoT-derived spelling argparse accepts. Resolve to the inner
        # name so the id check below still applies.
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id in {"list", "tuple"}
            and len(value.args) == 1
            and isinstance(value.args[0], ast.Name)
        ):
            return ("name", None, value.args[0].id, value.lineno)
        # Literal list/tuple of string constants.
        if isinstance(value, (ast.Tuple, ast.List)):
            members: list[str] = []
            for elt in value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    members.append(elt.value)
                else:
                    return ("other", None, None, value.lineno)
            return ("literal", members, None, value.lineno)
        return ("other", None, None, value.lineno)
    return ("missing", None, None, None)


def main() -> int:
    if not PY_KINDS.exists():
        print(f"FAIL: missing {PY_KINDS}", file=sys.stderr)
        return 1
    if not IMPORT_CLI.exists():
        print(f"FAIL: missing {IMPORT_CLI}", file=sys.stderr)
        return 1

    work_kinds, segment_of = _load_kinds()
    tree = ast.parse(IMPORT_CLI.read_text(encoding="utf-8"))

    failures: list[str] = []

    # (2) projects are sections, not works.
    if "project" in work_kinds:
        failures.append(
            f'"project" is in WORK_KINDS ({work_kinds!r}) — projects are themed '
            "sections, not convertible/downloadable works (PAN004/PAN015)."
        )

    # (3) every work kind must route (subset of SEGMENT_OF keys).
    missing_segments = [k for k in work_kinds if k not in segment_of]
    if missing_segments:
        failures.append(
            f"WORK_KINDS is not a subset of SEGMENT_OF: {missing_segments} have no "
            f"URL segment (SEGMENT_OF keys: {sorted(segment_of)})."
        )

    # (1) the import CLI's --kind choices must equal WORK_KINDS.
    form, members, name_id, lineno = _kind_choices(tree)
    loc = f"scripts/import_docx.py:{lineno}" if lineno else "scripts/import_docx.py"
    if form == "missing":
        failures.append(
            f"{loc}: no `add_argument(\"{KIND_OPTION}\", …, choices=…)` found — the "
            "import CLI must constrain --kind to WORK_KINDS."
        )
    elif form == "other":
        failures.append(
            f"{loc}: `{KIND_OPTION}` choices is not WORK_KINDS nor a string-literal "
            "list/tuple; the audit can't prove it equals the WORK_KINDS SoT."
        )
    elif form == "name":
        # choices is a (possibly list()/tuple()-wrapped) name. It must be the SoT
        # name itself AND that name must be imported from lib.kinds — so a decoy
        # `from lib.kinds import WORK_KINDS` sitting next to `choices=OTHER_TUPLE`
        # cannot pass the guard.
        if name_id != SOT_NAME:
            failures.append(
                f"{loc}: `{KIND_OPTION}` choices is `{name_id}`, not the `{SOT_NAME}` "
                "source of truth — choices must derive from lib.kinds.WORK_KINDS, not a "
                "separate local kind list."
            )
        elif not _imports_sot_from_kinds(tree):
            failures.append(
                f"{loc}: `{KIND_OPTION}` choices is {SOT_NAME} but import_docx.py does not "
                f"`from lib.kinds import {SOT_NAME}` — choices must derive from the SoT."
            )
    elif form == "literal":
        assert members is not None
        if tuple(members) != tuple(work_kinds):
            failures.append(
                f"{loc}: `{KIND_OPTION}` choices {tuple(members)!r} != WORK_KINDS "
                f"{tuple(work_kinds)!r} — the import CLI hardcodes a kind list that "
                "disagrees with the WORK_KINDS source of truth."
            )

    # (4) the CLI door must not redeclare a drifting --kind. It is OPTIONAL in a
    # fixture tree; when present it must DEFER (no --kind of its own) or DERIVE
    # (choices == WORK_KINDS, imported from lib.kinds) — the same standard the
    # importer's own --kind is held to, so the boundary holds on the CLI surface.
    if CLI_DOOR.exists():
        cli_tree = ast.parse(CLI_DOOR.read_text(encoding="utf-8"))
        cli_form, cli_members, cli_name_id, cli_lineno = _kind_choices(cli_tree)
        cli_loc = f"pancratius/cli.py:{cli_lineno}" if cli_lineno else "pancratius/cli.py"
        if cli_form == "missing":
            pass  # the door defers --kind to the importer entry — the encouraged state
        elif cli_form == "other":
            failures.append(
                f"{cli_loc}: the CLI door's `{KIND_OPTION}` choices is not WORK_KINDS nor a "
                "string-literal list/tuple; the audit can't prove it equals the WORK_KINDS SoT."
            )
        elif cli_form == "name":
            if cli_name_id != SOT_NAME:
                failures.append(
                    f"{cli_loc}: the CLI door's `{KIND_OPTION}` choices is `{cli_name_id}`, not the "
                    f"`{SOT_NAME}` source of truth — the door must defer to the importer entry or "
                    "derive choices from lib.kinds.WORK_KINDS."
                )
            elif not _imports_sot_from_kinds(cli_tree):
                failures.append(
                    f"{cli_loc}: the CLI door's `{KIND_OPTION}` choices is {SOT_NAME} but "
                    f"pancratius/cli.py does not `from lib.kinds import {SOT_NAME}` — choices must "
                    "derive from the SoT."
                )
        elif cli_form == "literal":
            assert cli_members is not None
            if tuple(cli_members) != tuple(work_kinds):
                failures.append(
                    f"{cli_loc}: the CLI door redeclares `{KIND_OPTION}` choices {tuple(cli_members)!r} "
                    f"!= WORK_KINDS {tuple(work_kinds)!r} — it must defer to the importer entry, not "
                    "hardcode a divergent kind list."
                )

    if failures:
        print("FAIL: import work-kinds boundary violated", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 1

    door = "; CLI door defers/derives --kind" if CLI_DOOR.exists() else ""
    print(
        f"PASS: import --kind choices derive from WORK_KINDS {tuple(work_kinds)!r}; "
        f"project excluded; WORK_KINDS ⊆ SEGMENT_OF{door}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
