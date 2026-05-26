"""PAN017 — import work-kinds guard (retired-capability surface, PAN015 family).

``pancratius work import`` converts corpus WORKS only. The convertible corpus work
kinds have one source of truth: ``CORPUS_WORK_KINDS`` in ``pancratius/kinds.py``.
This audit asserts the public CLI derives its ``--kind`` choices from that SoT
rather than hardcoding a (possibly project-including) list, and that the SoT
itself stays coherent with the routing map:

  1. ``pancratius/cli.py``'s ``work import --kind`` argparse ``choices`` derives
     from ``CORPUS_WORK_KINDS``;
  2. ``"project" not in CORPUS_WORK_KINDS`` — projects are themed sections, not works,
     so they must never be an importable/convertible kind (PAN004 / PAN015);
  3. ``CORPUS_WORK_KINDS`` is a subset of ``SEGMENT_OF`` keys — every work kind still
     routes (``SEGMENT_OF`` deliberately also carries ``project`` for routing);

It derives, it does not restate (PAN003): the kinds come from ``kinds.py`` and
the choices come from the public CLI's own argparse, so re-adding ``project`` to
the import surface trips the rule because the two disagree — not because the
rule hardcodes a banned value.

Honours ``PANCRATIUS_AUDIT_ROOT`` (the harness points it at a fixture);
wrapped as PAN017 in audit/rules/imports.ts.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import sys
from pathlib import Path


def _audit_root() -> Path:
    """The tree to scan: the fixture root when ``PANCRATIUS_AUDIT_ROOT`` is set,
    else the repo root."""
    env = os.environ.get("PANCRATIUS_AUDIT_ROOT")
    return Path(env).resolve() if env else Path(__file__).resolve().parents[2]


ROOT = _audit_root()
PY_KINDS = ROOT / "pancratius" / "kinds.py"
CLI_DOOR = ROOT / "pancratius" / "cli.py"

# The argparse option whose `choices` must equal CORPUS_WORK_KINDS, and the SoT name the
# CLI is expected to reference for those choices.
KIND_OPTION = "--kind"
SOT_NAME = "CORPUS_WORK_KINDS"


def _load_kinds() -> tuple[tuple[str, ...], dict[str, str]]:
    """Return (CORPUS_WORK_KINDS, SEGMENT_OF) from pancratius/kinds.py."""
    spec = importlib.util.spec_from_file_location("pancratius_kinds", PY_KINDS)
    if spec is None or spec.loader is None:
        raise ValueError(f"could not load {PY_KINDS}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return tuple(module.CORPUS_WORK_KINDS), dict(module.SEGMENT_OF)


def _imports_sot_from_kinds(tree: ast.Module) -> bool:
    """True if the module imports the corpus work-kinds SoT from pancratius.kinds."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "pancratius.kinds":
            if any(alias.name == SOT_NAME for alias in node.names):
                return True
    return False


def _kind_choices(tree: ast.Module) -> tuple[str, list[str] | None, str | None, int | None]:
    """Find the `add_argument("--kind", … choices=… )` call and describe its
    `choices` value. Returns (form, literal_members, name_id, lineno):

    - form == "name":    choices is a bare Name, or `list(Name)` / `tuple(Name)`
                         (e.g. CORPUS_WORK_KINDS); name_id is that Name's identifier.
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
        # Bare name: `choices=CORPUS_WORK_KINDS`.
        if isinstance(value, ast.Name):
            return ("name", None, value.id, value.lineno)
        # Wrapped name: `choices=list(CORPUS_WORK_KINDS)` / `tuple(CORPUS_WORK_KINDS)` — a
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
    if not CLI_DOOR.exists():
        print(f"FAIL: missing {CLI_DOOR}", file=sys.stderr)
        return 1

    work_kinds, segment_of = _load_kinds()
    tree = ast.parse(CLI_DOOR.read_text(encoding="utf-8"))

    failures: list[str] = []

    # (2) projects are sections, not works.
    if "project" in work_kinds:
        failures.append(
            f'"project" is in CORPUS_WORK_KINDS ({work_kinds!r}) — projects are themed '
            "sections, not convertible/downloadable works (PAN004/PAN015)."
        )

    # (3) every work kind must route (subset of SEGMENT_OF keys).
    missing_segments = [k for k in work_kinds if k not in segment_of]
    if missing_segments:
        failures.append(
            f"CORPUS_WORK_KINDS is not a subset of SEGMENT_OF: {missing_segments} have no "
            f"URL segment (SEGMENT_OF keys: {sorted(segment_of)})."
        )

    # (1) the public import command's --kind choices must derive from CORPUS_WORK_KINDS.
    form, members, name_id, lineno = _kind_choices(tree)
    loc = f"pancratius/cli.py:{lineno}" if lineno else "pancratius/cli.py"
    if form == "missing":
        failures.append(
            f"{loc}: no `add_argument(\"{KIND_OPTION}\", …, choices=…)` found — the "
            "public import command must constrain --kind to CORPUS_WORK_KINDS."
        )
    elif form == "other":
        failures.append(
            f"{loc}: `{KIND_OPTION}` choices is not CORPUS_WORK_KINDS nor a string-literal "
            "list/tuple; the audit can't prove it equals the corpus work-kinds SoT."
        )
    elif form == "name":
        # choices is a (possibly list()/tuple()-wrapped) name. It must be the SoT
        # name itself AND that name must be imported from pancratius.kinds — so a decoy
        # `from pancratius.kinds import CORPUS_WORK_KINDS` sitting next to `choices=OTHER_TUPLE`
        # cannot pass the guard.
        if name_id != SOT_NAME:
            failures.append(
                f"{loc}: `{KIND_OPTION}` choices is `{name_id}`, not the `{SOT_NAME}` "
                "source of truth — choices must derive from pancratius.kinds.CORPUS_WORK_KINDS, not a "
                "separate local kind list."
            )
        elif not _imports_sot_from_kinds(tree):
            failures.append(
                f"{loc}: `{KIND_OPTION}` choices is {SOT_NAME} but pancratius/cli.py does not "
                f"`from pancratius.kinds import {SOT_NAME}` — choices must derive from the SoT."
            )
    elif form == "literal":
        assert members is not None
        if tuple(members) != tuple(work_kinds):
            failures.append(
                f"{loc}: `{KIND_OPTION}` choices {tuple(members)!r} != CORPUS_WORK_KINDS "
                f"{tuple(work_kinds)!r} — the public import command hardcodes a kind list that "
                "disagrees with the corpus work-kinds source of truth."
            )

    if failures:
        print("FAIL: import work-kinds boundary violated", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 1

    print(
        f"PASS: `pancratius work import --kind` derives from CORPUS_WORK_KINDS {tuple(work_kinds)!r}; "
        "project excluded; CORPUS_WORK_KINDS ⊆ SEGMENT_OF"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
