from __future__ import annotations

import shutil
from pathlib import Path
from zipfile import ZipFile

from pancratius.render_downloads import WorkEntry, discover_works, render_epub, render_pdf


def _copy_representative_book(destination: Path) -> WorkEntry:
    source = next(
        entry
        for entry in discover_works()
        if entry.kind == "book" and entry.number == 1 and entry.lang == "ru"
    )
    destination.mkdir()

    markdown = destination / source.md.name
    shutil.copy2(source.md, markdown)

    if source.cover is not None:
        shutil.copy2(source.cover, destination / source.cover.name)

    images = source.folder / "images"
    if images.is_dir():
        shutil.copytree(images, destination / "images")

    return WorkEntry(
        kind=source.kind,
        number=source.number,
        folder=destination,
        lang=source.lang,
        md=markdown,
        slug=source.slug,
        title=source.title,
    )


def test_locked_renderers_produce_pdf_and_epub(tmp_path: Path) -> None:
    entry = _copy_representative_book(tmp_path / "book-1")
    assert entry.cover is not None

    scratch = tmp_path / "scratch"

    pdf = render_pdf(entry, scratch)
    epub = render_epub(entry, scratch)

    assert pdf.read_bytes().startswith(b"%PDF-")
    assert pdf.stat().st_size > 1_000

    with ZipFile(epub) as archive:
        assert archive.testzip() is None
        assert archive.read("mimetype") == b"application/epub+zip"
        assert "META-INF/container.xml" in archive.namelist()
