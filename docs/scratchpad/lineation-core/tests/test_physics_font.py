# research-pure: guards the metric oracle — the vendored font the physics simulator measures with.
"""The `fill`/`wraps` signal is only as faithful as Liberation Serif's advances, so two things must
hold: the vendored bytes match the pin (import would already fail loud otherwise — asserted plainly
here for a readable message), and the pin still matches the LibreOffice the corpus is laid out in.
The second is the drift guard: a LibreOffice upgrade that ships a re-metricked Liberation fails this
test instead of silently shifting every label. Skipped where that bundle is absent (CI / Linux)."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from lineation_core import physics

# Where LibreOffice ships its default serif on macOS — the renderer the gold corpus was produced
# against. Absent off-macOS, where the drift check simply can't run.
_LIBREOFFICE_SERIF = Path(
    "/Applications/LibreOffice.app/Contents/Resources/fonts/truetype/LiberationSerif-Regular.ttf")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_vendored_font_matches_pin() -> None:
    assert physics._LIBERATION_SERIF.is_file(), "vendored metric font is missing"
    assert _sha256(physics._LIBERATION_SERIF) == physics._LIBERATION_SHA256


def test_pin_matches_libreoffice_bundle() -> None:
    if not _LIBREOFFICE_SERIF.is_file():
        pytest.skip("LibreOffice not installed at the macOS bundle path")
    assert _sha256(_LIBREOFFICE_SERIF) == physics._LIBERATION_SHA256, (
        "vendored metric font has drifted from the installed LibreOffice — re-pin only after "
        "confirming the gold corpus is re-rendered against the new Liberation metrics")
