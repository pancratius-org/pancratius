"""Focused coverage for PAN012's workflow-door contract."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "audit" / "python" / "ci_separation.py"


def _workflow(root: Path, run: str, *, filename: str = "pr.yml") -> Path:
    workflows = root / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / filename).write_text(
        "\n".join((
            "name: Build",
            "on: [push]",
            "jobs:",
            "  build:",
            "    runs-on: ubuntu-latest",
            "    steps:",
            "      - uses: actions/checkout@v6",
            "      - name: Probe",
            f"        run: {run}",
            "",
        )),
        encoding="utf-8",
    )
    return root


def _mise_workflow(root: Path, *, version: str | None, install_args: str | None) -> Path:
    workflows = root / ".github" / "workflows"
    workflows.mkdir(parents=True)
    with_lines = ["        with:"]
    if version is not None:
        with_lines.append(f'          version: "{version}"')
    if install_args is not None:
        with_lines.append(f'          install_args: "{install_args}"')
    (workflows / "pr.yml").write_text(
        "\n".join((
            "name: Build",
            "on: [push]",
            "jobs:",
            "  build:",
            "    runs-on: ubuntu-latest",
            "    steps:",
            "      - name: Setup locked toolchain",
            "        uses: jdx/mise-action@0123456789abcdef0123456789abcdef01234567",
            *with_lines,
            "",
        )),
        encoding="utf-8",
    )
    return root


def _mise_settings(root: Path, *, auto_install: bool | None) -> Path:
    lines = (
        []
        if auto_install is None
        else ["[settings]", f"task.run_auto_install = {str(auto_install).lower()}"]
    )
    (root / "mise.toml").write_text("\n".join((*lines, "")), encoding="utf-8")
    return root


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER)],
        env={"PANCRATIUS_AUDIT_ROOT": str(root), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("filename", "command"),
    (
        ("pr.yml", "npm ci"),
        ("video-sync.yml", "uv run --frozen pancratius video sync"),
    ),
)
def test_allowed_direct_workflow_commands(
    tmp_path: Path,
    filename: str,
    command: str,
) -> None:
    proc = _run(_workflow(tmp_path, command, filename=filename))
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize(
    ("filename", "command", "message"),
    (
        ("pr.yml", "uv run pancratius/ir/nodes.py", "direct pancratius library access"),
        (
            "pr.yml",
            "uv run python -c 'from pancratius.passes.pipeline import run'",
            "direct pancratius library access",
        ),
        (
            "pr.yml",
            "uv run --frozen pancratius work import a.docx --kind book",
            "direct pancratius library access",
        ),
        (
            "pr.yml",
            "uv run --frozen pancratius video sync",
            "direct pancratius library access",
        ),
        (
            "video-sync.yml",
            "uv run --frozen pancratius video sync --dry-run",
            "direct pancratius library access",
        ),
        ("pr.yml", "mise --locked bootstrap --yes", "mise bootstrap"),
        ("pr.yml", "npm run build", "must enter through mise"),
        (
            "pr.yml",
            "uv run --project intent-ai python -m intent_ai.build_records",
            "intent-ai record compiler",
        ),
    ),
)
def test_rejected_direct_workflow_commands(
    tmp_path: Path,
    filename: str,
    command: str,
    message: str,
) -> None:
    proc = _run(_workflow(tmp_path, command, filename=filename))
    assert proc.returncode == 1
    assert message in proc.stderr


def test_mise_action_with_exact_ci_tool_subset_is_allowed(tmp_path: Path) -> None:
    root = _mise_workflow(tmp_path, version="2026.7.5", install_args="node python uv")
    proc = _run(root)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize(
    ("version", "install_args", "message"),
    (
        ("2026.7.5", None, "install_args must be exactly 'node python uv'"),
        (
            "2026.7.5",
            "node python uv pandoc typst",
            "install_args must be exactly 'node python uv'",
        ),
        ("latest", "node python uv", "version must be an exact release"),
    ),
)
def test_invalid_mise_action_configuration_is_rejected(
    tmp_path: Path,
    version: str,
    install_args: str | None,
    message: str,
) -> None:
    root = _mise_workflow(tmp_path, version=version, install_args=install_args)
    proc = _run(root)
    assert proc.returncode == 1
    assert message in proc.stderr


@pytest.mark.parametrize("task", ("verify", "verify:content"))
def test_ci_can_enter_locked_public_gate(tmp_path: Path, task: str) -> None:
    root = _mise_settings(
        _workflow(tmp_path, f"mise --locked run {task}"),
        auto_install=False,
    )
    proc = _run(root)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize(
    ("command", "message"),
    (
        ("mise --locked run check", "only mise tasks: verify, verify:content"),
        ("mise --locked run test:renderers", "only mise tasks: verify, verify:content"),
        ("mise run verify", "mise task calls must be exactly"),
        ("mise --locked run verify --force", "mise task calls must be exactly"),
    ),
)
def test_invalid_ci_mise_task_call_is_rejected(
    tmp_path: Path,
    command: str,
    message: str,
) -> None:
    root = _mise_settings(
        _workflow(tmp_path, command),
        auto_install=False,
    )
    proc = _run(root)
    assert proc.returncode == 1
    assert message in proc.stderr


@pytest.mark.parametrize("auto_install", (None, True))
def test_ci_mise_tasks_require_disabled_auto_install(
    tmp_path: Path,
    *,
    auto_install: bool | None,
) -> None:
    root = _mise_settings(
        _workflow(tmp_path, "mise --locked run verify"),
        auto_install=auto_install,
    )
    proc = _run(root)
    assert proc.returncode == 1
    assert "settings.task.run_auto_install=false" in proc.stderr
