"""Focused coverage for PAN012's CI/library-door boundary scan.

The audit harness selftest proves each rule fires somewhere. These tests isolate
the nested IR/package paths so a broad bad fixture cannot hide a stale guard.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "audit" / "python" / "ci_separation.py"


def _workflow(root: Path, run: str) -> Path:
    workflows = root / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "pr.yml").write_text(
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


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER)],
        env={"PANCRATIUS_AUDIT_ROOT": str(root), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )


def test_nested_ir_nodes_path_is_banned_in_isolation(tmp_path: Path) -> None:
    proc = _run(_workflow(tmp_path, "uv run pancratius/ir/nodes.py"))
    assert proc.returncode == 1
    assert "converter/IR/writer module" in proc.stderr


def test_nested_ir_normalize_path_is_banned_in_isolation(tmp_path: Path) -> None:
    proc = _run(_workflow(tmp_path, "uv run pancratius/ir/normalize.py"))
    assert proc.returncode == 1
    assert "converter/IR/writer module" in proc.stderr


def test_nested_ir_lower_path_is_banned_in_isolation(tmp_path: Path) -> None:
    proc = _run(_workflow(tmp_path, "uv run pancratius/ir/lower.py"))
    assert proc.returncode == 1
    assert "converter/IR/writer module" in proc.stderr


def test_nested_ir_nodes_dotted_import_is_banned_in_isolation(tmp_path: Path) -> None:
    proc = _run(_workflow(tmp_path, "uv run python -c 'import pancratius.ir.nodes'"))
    assert proc.returncode == 1
    assert "converter/IR/writer module" in proc.stderr


def test_nested_ir_normalize_dotted_import_is_banned_in_isolation(tmp_path: Path) -> None:
    proc = _run(_workflow(tmp_path, "uv run python -c 'from pancratius.ir.normalize import normalize'"))
    assert proc.returncode == 1
    assert "converter/IR/writer module" in proc.stderr


def test_nested_ir_lower_dotted_import_is_banned_in_isolation(tmp_path: Path) -> None:
    proc = _run(_workflow(tmp_path, "uv run python -c 'import pancratius.ir.lower'"))
    assert proc.returncode == 1
    assert "converter/IR/writer module" in proc.stderr


def test_console_script_corpus_verb_is_banned_in_isolation(tmp_path: Path) -> None:
    proc = _run(_workflow(tmp_path, "uv run --frozen pancratius work import source.docx --kind book"))
    assert proc.returncode == 1
    assert "pancratius corpus-management CLI" in proc.stderr


def test_site_build_command_is_allowed(tmp_path: Path) -> None:
    proc = _run(_workflow(tmp_path, "npm run build"))
    assert proc.returncode == 0, proc.stderr
