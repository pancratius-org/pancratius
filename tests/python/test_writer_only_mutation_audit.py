"""Focused type-aware checks for the writer-only filesystem boundary."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "audit" / "python" / "writer_only_mutation.py"
MARKER = "# import-pure: no filesystem mutation\n"


def _check(tmp_path: Path, source: str) -> subprocess.CompletedProcess[str]:
    module = tmp_path / "pancratius" / "pure.py"
    module.parent.mkdir(parents=True)
    module.write_text(MARKER + source, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(CHECKER)],
        env={"PANCRATIUS_AUDIT_ROOT": str(tmp_path), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )


def test_value_replacement_is_not_a_filesystem_mutation(tmp_path: Path) -> None:
    result = _check(
        tmp_path,
        "def normalize(value: str) -> str:\n"
        "    return value.replace('_', ' ')\n"
        "\n"
        "def collections(values: list[str]) -> list[str]:\n"
        "    copied = values.copy()\n"
        "    copied.remove('probe')\n"
        "    return copied\n",
    )

    assert result.returncode == 0, result.stderr


def test_qualified_os_replace_is_a_filesystem_mutation(tmp_path: Path) -> None:
    result = _check(
        tmp_path,
        "import os as operating_system\n"
        "\n"
        "def publish(source: str, target: str) -> None:\n"
        "    operating_system.replace(source, target)\n",
    )

    assert result.returncode == 1
    assert "os.replace(...)" in result.stderr


def test_path_replace_is_a_filesystem_mutation(tmp_path: Path) -> None:
    result = _check(
        tmp_path,
        "from pathlib import Path\n"
        "\n"
        "def publish(source: Path, target: Path) -> None:\n"
        "    source.replace(target)\n",
    )

    assert result.returncode == 1
    assert "Path.replace(...)" in result.stderr
