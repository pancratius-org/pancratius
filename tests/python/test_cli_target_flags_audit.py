"""Isolated polarity coverage for PAN024's public CLI target-flag scan."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "audit" / "python" / "cli_target_flags.py"

_CLI_GOOD = (
    "from __future__ import annotations\n"
    "import argparse\n"
    "def add(ap: argparse.ArgumentParser) -> None:\n"
    "    ap.add_argument('selectors', nargs='*')\n"
    "    ap.add_argument('--books-root')\n"
)

_CLI_BAD = (
    "from __future__ import annotations\n"
    "import argparse\n"
    "def add(ap: argparse.ArgumentParser) -> None:\n"
    "    ap.add_argument('--book', type=int)\n"
    "    ap.add_argument('--poem', type=int)\n"
    "    ap.add_argument('--number', type=int)\n"
    "    ap.add_argument('--into')\n"
)


def _tree(base: Path, cli_src: str) -> Path:
    (base / "pancratius").mkdir(parents=True)
    (base / "pancratius" / "cli.py").write_text(cli_src, encoding="utf-8")
    return base


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER)],
        env={"PANCRATIUS_AUDIT_ROOT": str(root), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_target_selector_surface_passes(tmp_path: Path) -> None:
    proc = _run(_tree(tmp_path, _CLI_GOOD))
    assert proc.returncode == 0, proc.stderr


def test_cli_target_flags_fire_in_isolation(tmp_path: Path) -> None:
    proc = _run(_tree(tmp_path, _CLI_BAD))
    assert proc.returncode == 1
    assert "--book" in proc.stderr
    assert "--poem" in proc.stderr
    assert "--number" in proc.stderr
    assert "--into" in proc.stderr
