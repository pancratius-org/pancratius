from __future__ import annotations

from pathlib import Path
import importlib.util
import shutil
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import import_docx  # noqa: E402
from lib.content_catalog import split_frontmatter  # noqa: E402


pytestmark = pytest.mark.skipif(
    shutil.which("pandoc") is None or importlib.util.find_spec("PIL") is None,
    reason="pandoc and pillow are required",
)


def _run_import(content_root: Path, *args: str) -> import_docx.ImportResult:
    parsed = import_docx.build_parser().parse_args([*args, "--out-content", str(content_root)])
    return import_docx.run(parsed)


def _frontmatter(path: Path) -> dict:
    fm, _body = split_frontmatter(path.read_text(encoding="utf-8"))
    return fm


def test_import_new_docx_creates_bundle_paths_and_frontmatter(tmp_path: Path) -> None:
    content_root = tmp_path / "src" / "content"
    result = _run_import(
        content_root,
        str(ROOT / "legacy/books/ru/23-личность-и-эго.docx"),
        "--kind", "book",
        "--lang", "ru",
        "--number", "90",
        "--slug", "probe-work",
        "--title", "Probe Work",
        "--description", "Probe description.",
    )

    work_dir = content_root / "books" / "90-probe-work"
    assert result.work_key == "90-probe-work"
    assert result.md_path == work_dir / "ru.md"
    assert (work_dir / "ru.md").is_file()
    assert (work_dir / "ru.docx").is_file()

    fm = _frontmatter(work_dir / "ru.md")
    assert fm["kind"] == "book"
    assert fm["number"] == 90
    assert fm["slug"] == "90-probe-work"
    assert fm["title"] == "Probe Work"
    assert fm["lang"] == "ru"
    assert fm["description"] == "Probe description."
    assert fm["translation"] == {"source": "original"}


def test_import_translation_with_into_updates_existing_bundle(tmp_path: Path) -> None:
    content_root = tmp_path / "src" / "content"
    work_dir = content_root / "books" / "30-poslanie-musulmanam"
    work_dir.mkdir(parents=True)
    (work_dir / "ru.md").write_text(
        """---
kind: book
number: 30
slug: 30-poslanie-musulmanam
title: Послание мусульманам
lang: ru
description: Russian description.
tags:
- Откровение Бога
- ислам
cover: null
translation:
  source: original
---

Existing body.
""",
        encoding="utf-8",
    )

    _run_import(
        content_root,
        str(ROOT / "legacy/books/en/30-послание-мусульманам.docx"),
        "--into", "30-poslanie-musulmanam",
        "--lang", "en",
    )

    assert sorted(path.name for path in (content_root / "books").iterdir()) == ["30-poslanie-musulmanam"]
    assert (work_dir / "ru.md").is_file()
    assert (work_dir / "en.md").is_file()
    assert (work_dir / "en.docx").is_file()

    fm = _frontmatter(work_dir / "en.md")
    assert fm["kind"] == "book"
    assert fm["number"] == 30
    assert fm["slug"] == "30-poslanie-musulmanam"
    assert fm["lang"] == "en"
    assert fm["tags"] == ["Откровение Бога", "ислам"]
    assert fm["description"].startswith("TODO:")
    assert fm["translation"] == {"source": "ai"}


def test_importer_does_not_consult_legacy_catalog_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The legacy catalog loaders no longer exist (the batch CLI is gone); the
    # remaining guard is the only one that matters: nothing in the import path may
    # read anything under legacy/data.
    original_read_text = Path.read_text

    def guarded_read_text(self: Path, *args: str | None, **kwargs: str | None) -> str:
        if "legacy/data" in self.as_posix():
            raise AssertionError(f"legacy catalog read attempted: {self}")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)

    content_root = tmp_path / "src" / "content"
    _run_import(
        content_root,
        str(ROOT / "legacy/books/ru/30-послание-мусульманам.docx"),
        "--kind", "book",
        "--lang", "ru",
        "--number", "7",
        "--slug", "frontmatter-only",
        "--title", "Frontmatter Only",
        "--description", "From overrides.",
    )

    fm = _frontmatter(content_root / "books" / "07-frontmatter-only" / "ru.md")
    assert fm["number"] == 7
    assert fm["slug"] == "07-frontmatter-only"
    assert fm["title"] == "Frontmatter Only"
