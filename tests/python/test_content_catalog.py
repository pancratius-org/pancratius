from __future__ import annotations

from pathlib import Path

import pytest

from pancratius.content_catalog import CatalogError, scan_catalog


def _write_book(root: Path, *, stem: str, lang: str | None = None) -> Path:
    work = root / "books" / "01-test"
    work.mkdir(parents=True, exist_ok=True)
    md = work / f"{stem}.md"
    lang_line = f"lang: {lang}\n" if lang is not None else ""
    md.write_text(
        f"""---
kind: book
number: 1
slug: 01-test
title: Test
{lang_line}description: Test.
---

Body.
""",
        encoding="utf-8",
    )
    return md


def test_scan_catalog_accepts_locale_from_file_stem(tmp_path: Path) -> None:
    content = tmp_path / "src" / "content"
    _write_book(content, stem="ru")

    entries = scan_catalog(content)

    assert len(entries) == 1
    assert entries[0].lang == "ru"


def test_scan_catalog_rejects_unsupported_locale(tmp_path: Path) -> None:
    content = tmp_path / "src" / "content"
    md = _write_book(content, stem="fr")

    with pytest.raises(CatalogError, match=f"{md}: unsupported locale 'fr'"):
        scan_catalog(content)
