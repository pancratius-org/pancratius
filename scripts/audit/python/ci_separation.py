#!/usr/bin/env -S uv run --quiet
"""PAN012 — CI import/render/build separation.

CI builds and PUBLISHES the site; it never manufactures the library
(architecture.md "Shape"; downloads.md "CI Contract"). So a CI workflow must not
install or run the library-management tooling — pandoc, typst, the embedding
stack, DOCX optimizers, or the source importers/renderers. Those are local/admin
activities that mutate source or render release artifacts.

This parses the workflow YAML with PyYAML and scans only the `run:` and `uses:`
of each step — NOT comments or surrounding prose — so build.yml's own
"MUST NOT install pandoc or typst" comment is not a false hit. Honours
``PANCRATIUS_AUDIT_ROOT`` (the harness points it at a fixture); wrapped by the
TS harness as PAN012 (scripts/audit/rules/ownership.ts).
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import cast

import yaml


def audit_root() -> Path:
    env = os.environ.get("PANCRATIUS_AUDIT_ROOT")
    if env:
        return Path(env).resolve()
    # scripts/audit/python/ci_separation.py -> repo root is four levels up.
    return Path(__file__).resolve().parents[3]


# Banned in a step's `run:` command. Each is a durable contract from
# architecture.md ("Library-management tools": pandoc, typst, …; "Run via uv
# only" → no pip/conda) and downloads.md ("CI Contract"): the render/convert
# engines, the banned Python install mechanisms, and the mutating import/render
# scripts (the library door, never CI). Word-boundaried so substrings don't
# misfire (e.g. a path containing "pip").
# Case-insensitive throughout (a `Pandoc` is as banned as `pandoc`); word-bounded
# so substrings ("typescript", "pipx", "typstyle") don't misfire.
_F = re.IGNORECASE
_RUN_BANNED: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pandoc (document converter)", re.compile(r"\bpandoc\b", _F)),
    ("typst (PDF engine)", re.compile(r"\btypst\b", _F)),
    ("pip install (banned: uv only)", re.compile(r"\bpip3?\s+install\b", _F)),
    ("uv pip install (banned: locked deps only)", re.compile(r"\buv\s+pip\b", _F)),
    ("conda (banned: uv only)", re.compile(r"\bconda\b", _F)),
    ("requirements.txt (banned: uv lock only)", re.compile(r"requirements\.txt", _F)),
    (
        "source importer / release renderer (library door, never CI)",
        re.compile(
            r"\b(import_docx|docx_to_md|render_downloads|docx_optimize"
            r"|conceptosphere|conceptosphere_embed)\.py\b",
            _F,
        ),
    ),
)

# Banned in a step's `uses:` (a setup action that installs an engine). Word-bounded
# so an unrelated action whose name merely contains the substring doesn't misfire.
_USES_BANNED: re.Pattern[str] = re.compile(r"\b(pandoc|typst)\b", _F)


# PyYAML returns `object`; `isinstance(x, dict)` alone narrows to a key/value-less
# dict the type checker won't let us `.get` on, so narrow-and-cast to the YAML
# mapping shape (str keys, arbitrary values) once, here.
def _as_mapping(value: object) -> dict[str, object] | None:
    return cast(dict[str, object], value) if isinstance(value, dict) else None


def _steps(workflow: object) -> list[dict[str, object]]:
    """Every step dict across all jobs, defensively (malformed YAML -> [])."""
    out: list[dict[str, object]] = []
    wf = _as_mapping(workflow)
    if wf is None:
        return out
    jobs = _as_mapping(wf.get("jobs"))
    if jobs is None:
        return out
    for job_value in jobs.values():
        job = _as_mapping(job_value)
        if job is None:
            continue
        steps = job.get("steps")
        if not isinstance(steps, list):
            continue
        for step_value in steps:
            step = _as_mapping(step_value)
            if step is not None:
                out.append(step)
    return out


def _scan_workflow(rel: str, text: str) -> list[str]:
    failures: list[str] = []
    try:
        workflow = yaml.safe_load(text)
    except yaml.YAMLError as exc:  # a workflow we can't parse is a failure to surface
        return [f"{rel}: could not parse workflow YAML ({exc})"]

    for step in _steps(workflow):
        name = step.get("name")
        label = f"{rel} step {name!r}" if isinstance(name, str) else rel

        run = step.get("run")
        if isinstance(run, str):
            for desc, pattern in _RUN_BANNED:
                if pattern.search(run):
                    failures.append(f"{label}: run uses {desc}")

        uses = step.get("uses")
        if isinstance(uses, str) and _USES_BANNED.search(uses):
            failures.append(f"{label}: uses action installs a banned engine ({uses})")

    return failures


def main() -> int:
    root = audit_root()
    workflows = root / ".github" / "workflows"
    if not workflows.is_dir():
        print(f"PASS: no {workflows.relative_to(root)} directory")
        return 0

    files = sorted(p for p in workflows.iterdir() if p.suffix in {".yml", ".yaml"})
    failures: list[str] = []
    for path in files:
        failures.extend(_scan_workflow(str(path.relative_to(root)), path.read_text(encoding="utf-8")))

    if failures:
        print("FAIL: CI runs library-management tooling (import/render/build separation)", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print(f"PASS: {len(files)} workflow(s) install/run no banned library tooling")
    return 0


if __name__ == "__main__":
    sys.exit(main())
