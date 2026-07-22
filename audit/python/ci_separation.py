"""PAN012 — keep library-manufacturing tools out of CI.

CI publishes committed library content. It may build and verify the site, but it
must not import DOCX, render release artifacts, optimize sources, or regenerate
research data.

This rule checks the boundary at the workflow door. GitHub Actions may enter the
repository task graph only through the locked ``verify`` and ``verify:content``
mise tasks. The mise action must install exactly Node, Python, and uv, and task
auto-install must remain disabled. Direct workflow commands are scanned for the
local library tools and mutation paths the boundary excludes.

PAN012 deliberately does not interpret mise tasks, npm scripts, included TOML,
or shell programs. Mise already owns task discovery and graph resolution; a
second partial interpreter here would be a weaker, drifting copy of that model.
"""

from __future__ import annotations

import os
import re
import sys
import tomllib
from pathlib import Path
from typing import cast

import yaml


def audit_root() -> Path:
    env = os.environ.get("PANCRATIUS_AUDIT_ROOT")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[2]


_F = re.IGNORECASE
_RUN_BANNED: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pandoc (document converter)", re.compile(r"\bpandoc\b", _F)),
    ("typst (PDF engine)", re.compile(r"\btypst\b", _F)),
    ("pip install (banned: uv only)", re.compile(r"\bpip3?\s+install\b", _F)),
    ("uv pip install (banned: locked deps only)", re.compile(r"\buv\s+pip\b", _F)),
    ("conda (banned: uv only)", re.compile(r"\bconda\b", _F)),
    ("requirements.txt (banned: uv lock only)", re.compile(r"requirements\.txt", _F)),
    (
        "local renderer smoke test",
        re.compile(r"\btests/tools/test_renderer_smoke\.py\b", _F),
    ),
    (
        "mise bootstrap (installs the full local toolchain)",
        re.compile(r"\bmise\b[^\n;&|]*\bbootstrap\b", _F),
    ),
    (
        "intent-ai record compiler (local research boundary, never CI)",
        re.compile(
            r"(?:\bintent_ai\.build_records\b"
            r"|\bintent-ai/src/intent_ai/build_records\.py\b)",
            _F,
        ),
    ),
)

_USES_BANNED = re.compile(r"\b(pandoc|typst)\b", _F)
_MISE_ACTION = re.compile(r"^jdx/mise-action@", _F)
_EXACT_MISE_VERSION = re.compile(r"^\d{4}\.\d+\.\d+$")
_CI_MISE_TOOLS = frozenset({"node", "python", "uv"})
_ALLOWED_CI_TASKS = frozenset({"verify", "verify:content"})
_CANONICAL_MISE_RUN = re.compile(r"mise --locked run ([\w:-]+)")
_ANY_MISE_RUN = re.compile(r"\bmise\b[^\n;&|]*\brun\b")
_NPM_RUN = re.compile(r"\bnpm\s+(?:run|run-script)\b")
_VIDEO_SYNC_WORKFLOW = ".github/workflows/video-sync.yml"
_VIDEO_SYNC_COMMAND = "uv run --frozen pancratius video sync"
_PANCRATIUS_REFERENCE = re.compile(r"\bpancratius(?:[./]|\s|$)")


def _as_mapping(value: object) -> dict[str, object] | None:
    return cast(dict[str, object], value) if isinstance(value, dict) else None


def _steps(workflow: object) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    wf = _as_mapping(workflow)
    jobs = _as_mapping(wf.get("jobs")) if wf is not None else None
    if jobs is None:
        return out
    for job_value in jobs.values():
        job = _as_mapping(job_value)
        steps = job.get("steps") if job is not None else None
        if not isinstance(steps, list):
            continue
        out.extend(step for value in steps if (step := _as_mapping(value)) is not None)
    return out


def _scan_run(rel: str, label: str, run: str) -> tuple[list[str], bool]:
    failures = [
        f"{label}: run uses {description}"
        for description, pattern in _RUN_BANNED
        if pattern.search(run)
    ]

    invokes_project_task = False
    for line in run.splitlines():
        command = line.strip()
        if not _ANY_MISE_RUN.search(command):
            continue
        call = _CANONICAL_MISE_RUN.fullmatch(command)
        if call is None:
            failures.append(
                f"{label}: mise task calls must be exactly 'mise --locked run verify' "
                "or 'mise --locked run verify:content'"
            )
            continue
        task = call.group(1)
        if task not in _ALLOWED_CI_TASKS:
            failures.append(
                f"{label}: CI may run only mise tasks: verify, verify:content (found {task})"
            )
            continue
        invokes_project_task = True

    if _NPM_RUN.search(run):
        failures.append(f"{label}: CI repository tasks must enter through mise, not npm run")

    without_video_sync = "\n".join(
        "" if rel == _VIDEO_SYNC_WORKFLOW and line.strip() == _VIDEO_SYNC_COMMAND else line
        for line in run.splitlines()
    )
    if _PANCRATIUS_REFERENCE.search(without_video_sync):
        failures.append(
            f"{label}: direct pancratius library access is forbidden; "
            "only the video sync workflow exception is allowed"
        )

    return failures, invokes_project_task


def _scan_mise_action(label: str, step: dict[str, object]) -> list[str]:
    with_ = _as_mapping(step.get("with"))
    if with_ is None:
        return [f"{label}: mise-action must install exactly: node python uv"]

    failures: list[str] = []
    version = with_.get("version")
    if not isinstance(version, str) or _EXACT_MISE_VERSION.fullmatch(version) is None:
        failures.append(f"{label}: mise-action version must be an exact release")

    install_args = with_.get("install_args")
    tools = frozenset(install_args.split()) if isinstance(install_args, str) else frozenset()
    if tools != _CI_MISE_TOOLS:
        actual = " ".join(sorted(tools)) if tools else "<missing>"
        failures.append(
            f"{label}: mise-action install_args must be exactly 'node python uv' (found {actual})"
        )
    return failures


def _scan_workflow(rel: str, text: str) -> tuple[list[str], bool]:
    try:
        workflow = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return [f"{rel}: could not parse workflow YAML ({exc})"], False

    failures: list[str] = []
    invokes_project_task = False
    for step in _steps(workflow):
        name = step.get("name")
        label = f"{rel} step {name!r}" if isinstance(name, str) else rel

        run = step.get("run")
        if isinstance(run, str):
            run_failures, invokes_mise = _scan_run(rel, label, run)
            failures.extend(run_failures)
            invokes_project_task = invokes_project_task or invokes_mise

        uses = step.get("uses")
        if isinstance(uses, str) and _USES_BANNED.search(uses):
            failures.append(f"{label}: uses action installs a banned engine ({uses})")
        if isinstance(uses, str) and _MISE_ACTION.search(uses):
            failures.extend(_scan_mise_action(label, step))

    return failures, invokes_project_task


def _auto_install_failure(root: Path) -> str | None:
    path = root / "mise.toml"
    try:
        config = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        return f"mise.toml: could not verify task.run_auto_install ({exc})"

    settings = _as_mapping(config.get("settings"))
    task = _as_mapping(settings.get("task")) if settings is not None else None
    if task is None or task.get("run_auto_install") is not False:
        return "mise.toml: CI mise tasks require settings.task.run_auto_install=false"
    return None


def main() -> int:
    root = audit_root()
    workflows = root / ".github" / "workflows"
    if not workflows.is_dir():
        print(f"PASS: no {workflows.relative_to(root)} directory")
        return 0

    files = sorted(path for path in workflows.iterdir() if path.suffix in {".yml", ".yaml"})
    failures: list[str] = []
    invokes_project_task = False
    for path in files:
        workflow_failures, invokes_mise = _scan_workflow(
            str(path.relative_to(root)), path.read_text(encoding="utf-8")
        )
        failures.extend(workflow_failures)
        invokes_project_task = invokes_project_task or invokes_mise

    if invokes_project_task and (failure := _auto_install_failure(root)) is not None:
        failures.append(failure)

    if failures:
        print("FAIL: CI crosses the library-management boundary", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print(f"PASS: {len(files)} workflow(s) preserve the CI/library boundary")
    return 0


if __name__ == "__main__":
    sys.exit(main())
