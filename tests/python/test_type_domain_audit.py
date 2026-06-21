from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "audit" / "python" / "type_domain.py"


def _write_baseline(root: Path, entries: list[str]) -> None:
    path = root / "data" / "type-domain-baseline.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"version": 1, "typescript": [], "python": entries}), encoding="utf-8")


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "PANCRATIUS_AUDIT_ROOT": str(root),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        check=False,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def test_type_domain_audit_flags_dataclass_domain_fields(tmp_path: Path) -> None:
    module = tmp_path / "pancratius" / "sample.py"
    module.parent.mkdir()
    module.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "from dataclasses import dataclass",
                "",
                "@dataclass(frozen=True)",
                "class ImportRequest:",
                "    lang: str",
            ]
        ),
        encoding="utf-8",
    )
    _write_baseline(tmp_path, [])

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "domain-field-primitive" in result.stdout
    assert "ImportRequest.lang" in result.stdout


def test_type_domain_audit_reports_stale_baseline_entries(tmp_path: Path) -> None:
    (tmp_path / "pancratius").mkdir()
    _write_baseline(
        tmp_path,
        ["py:domain-field-primitive:pancratius/sample.py:Missing.lang:str->Locale"],
    )

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "stale Python domain type-shape baseline entries" in result.stdout
    assert "pancratius/sample.py:Missing.lang" in result.stdout
