"""Isolated both-polarity coverage for PAN017's public CLI scan.

The audit harness selftest runs one good + one bad fixture per rule. These tests
run the PAN017 checker as a subprocess against crafted trees that isolate the
`pancratius work import --kind` surface, so the door's kind boundary cannot
silently rot.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "audit" / "python" / "import_work_kinds.py"

# A valid kinds SoT and a valid (deferring) importer, so the ONLY variable across
# cases is the CLI door — isolating the rule-4 (cli.py) scan.
_KINDS = (
    'SEGMENT_OF: dict[str, str] = {"book": "books", "poem": "poetry", "project": "projects"}\n'
    'CORPUS_WORK_KINDS: tuple[str, ...] = ("book", "poem")\n'
)
_CLI_DERIVES = (
    "from __future__ import annotations\n"
    "import argparse\n"
    "from pancratius.kinds import CORPUS_WORK_KINDS\n"
    "def add(ap: argparse.ArgumentParser) -> None:\n"
    "    ap.add_argument('--kind', choices=tuple(CORPUS_WORK_KINDS))\n"
)
_CLI_DRIFTS = (
    "from __future__ import annotations\n"
    "import argparse\n"
    "def add(ap: argparse.ArgumentParser) -> None:\n"
    "    ap.add_argument('--kind', choices=('book', 'poem', 'project'))\n"  # re-admits project
)
_CLI_MISSING = (
    "from __future__ import annotations\n"
    "import argparse\n"
    "def add(ap: argparse.ArgumentParser) -> None:\n"
    "    ap.add_argument('--lang')\n"
)


def _tree(base: Path, cli_src: str) -> Path:
    (base / "pancratius").mkdir(parents=True)
    (base / "pancratius" / "kinds.py").write_text(_KINDS, encoding="utf-8")
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


def test_cli_door_deriving_from_sot_passes(tmp_path: Path) -> None:
    proc = _run(_tree(tmp_path, _CLI_DERIVES))
    assert proc.returncode == 0, proc.stderr


def test_cli_door_drift_fires_in_isolation(tmp_path: Path) -> None:
    proc = _run(_tree(tmp_path, _CLI_DRIFTS))
    assert proc.returncode == 1
    assert "pancratius/cli.py" in proc.stderr


def test_cli_door_missing_kind_choices_fires(tmp_path: Path) -> None:
    proc = _run(_tree(tmp_path, _CLI_MISSING))
    assert proc.returncode == 1
    assert "no `add_argument" in proc.stderr
