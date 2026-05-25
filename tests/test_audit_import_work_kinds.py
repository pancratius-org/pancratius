"""Isolated both-polarity coverage for PAN017's NEW cli-door scan.

The audit harness selftest runs one good + one bad fixture per rule, so a single
bad fixture cannot independently prove that *each* scanned surface (the standalone
importer AND the `pancratius` CLI door) fires on its own violation — a broken
cli-door scan would be masked by the importer-drift fixture. These tests run the
PAN017 checker as a subprocess against crafted trees that isolate the cli-door
surface, so the door's `--kind` boundary cannot silently rot.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "audit" / "python" / "import_work_kinds.py"

# A valid kinds SoT and a valid (deferring) importer, so the ONLY variable across
# cases is the CLI door — isolating the rule-4 (cli.py) scan.
_KINDS = (
    'SEGMENT_OF: dict[str, str] = {"book": "books", "poem": "poetry", "project": "projects"}\n'
    'WORK_KINDS: tuple[str, ...] = ("book", "poem")\n'
)
_IMPORT_OK = (
    "from __future__ import annotations\n"
    "import argparse\n"
    "from lib.kinds import WORK_KINDS\n"
    "def build_parser() -> argparse.ArgumentParser:\n"
    "    ap = argparse.ArgumentParser()\n"
    "    ap.add_argument('docx')\n"
    "    ap.add_argument('--kind', choices=WORK_KINDS)\n"
    "    return ap\n"
)
_CLI_DEFERS = (
    "from __future__ import annotations\n"
    "import argparse\n"
    "def add(ap: argparse.ArgumentParser) -> None:\n"
    "    ap.add_argument('--lang')\n"  # no --kind: the door defers
)
_CLI_DRIFTS = (
    "from __future__ import annotations\n"
    "import argparse\n"
    "def add(ap: argparse.ArgumentParser) -> None:\n"
    "    ap.add_argument('--kind', choices=('book', 'poem', 'project'))\n"  # re-admits project
)


def _tree(base: Path, cli_src: str) -> Path:
    (base / "scripts" / "lib").mkdir(parents=True)
    (base / "pancratius").mkdir(parents=True)
    (base / "scripts" / "lib" / "kinds.py").write_text(_KINDS, encoding="utf-8")
    (base / "scripts" / "import_docx.py").write_text(_IMPORT_OK, encoding="utf-8")
    (base / "pancratius" / "cli.py").write_text(cli_src, encoding="utf-8")
    return base


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER)],
        env={"PANCRATIUS_AUDIT_ROOT": str(root), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )


def test_cli_door_deferring_passes(tmp_path: Path) -> None:
    proc = _run(_tree(tmp_path, _CLI_DEFERS))
    assert proc.returncode == 0, proc.stderr


def test_cli_door_drift_fires_in_isolation(tmp_path: Path) -> None:
    """Importer is valid; ONLY the door drifts — so a passing result would mean the
    cli-door scan never ran."""
    proc = _run(_tree(tmp_path, _CLI_DRIFTS))
    assert proc.returncode == 1
    assert "pancratius/cli.py" in proc.stderr
