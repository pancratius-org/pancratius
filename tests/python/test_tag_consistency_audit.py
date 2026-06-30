"""Focused coverage for PAN006C's per-locale tag-glossary scan.

The audit harness selftest proves the rule fires on one known-bad fixture (a raw
Russian tag on an English page). These tests isolate each dimension of the
contract — wrong case, an unglossaried tag on the Russian side, and a drifted
playlist title used as a tag — so a broad bad fixture cannot hide a stale guard
on the others now that the rule gates CI.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "audit" / "tag_consistency.py"

GLOSSARY = {
    "ru": {"свет": "свет", "истина": "истина"},
    "en": {"свет": "Light", "истина": "Truth"},
}


def _entry(kind: str, *, tags: Iterable[str] = (), playlists: Iterable[str] = ()) -> str:
    lines = ["---", f"kind: {kind}", "title: Fixture"]
    if tags := list(tags):
        lines += ["tags:", *(f"  - {t}" for t in tags)]
    if playlists := list(playlists):
        lines += ["playlists:", *(f"  - title: {t}" for t in playlists)]
    lines += ["---", "", "Body.", ""]
    return "\n".join(lines)


def _tree(root: Path, entries: Mapping[str, str], glossary: object = GLOSSARY) -> Path:
    data = root / "data"
    data.mkdir(parents=True)
    (data / "tag-glossary.yaml").write_text(yaml.safe_dump(glossary, allow_unicode=True), encoding="utf-8")
    for rel, text in entries.items():
        path = root / "src" / "content" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER)],
        env={"PANCRATIUS_AUDIT_ROOT": str(root), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )


def test_clean_corpus_passes(tmp_path: Path) -> None:
    proc = _run(_tree(tmp_path, {
        "books/01-x/ru.md": _entry("book", tags=["свет"]),
        "books/01-x/en.md": _entry("book", tags=["Light"]),
        "videos/clip/en.md": _entry("video", playlists=["Truth"]),
    }))
    assert proc.returncode == 0, proc.stderr


def test_russian_tag_leaks_onto_english_page(tmp_path: Path) -> None:
    proc = _run(_tree(tmp_path, {"books/01-x/en.md": _entry("book", tags=["свет"])}))
    assert proc.returncode == 1
    assert "свет" in proc.stderr


def test_wrong_case_english_tag_fires(tmp_path: Path) -> None:
    proc = _run(_tree(tmp_path, {"books/01-x/en.md": _entry("book", tags=["light"])}))
    assert proc.returncode == 1
    assert "light" in proc.stderr


def test_unglossaried_tag_on_russian_page_fires(tmp_path: Path) -> None:
    proc = _run(_tree(tmp_path, {"books/01-x/ru.md": _entry("book", tags=["тьма"])}))
    assert proc.returncode == 1
    assert "тьма" in proc.stderr


def test_drifted_playlist_title_fires(tmp_path: Path) -> None:
    proc = _run(_tree(tmp_path, {"videos/clip/en.md": _entry("video", playlists=["Unknown Playlist"])}))
    assert proc.returncode == 1
    assert "Unknown Playlist" in proc.stderr
