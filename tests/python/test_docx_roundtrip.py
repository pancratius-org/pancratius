from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

from pancratius.content_catalog import CatalogEntry, dump_frontmatter, scan_catalog
from pancratius.docx_roundtrip import (
    DocxRoundTripError,
    check_docx_markdown_roundtrip,
    check_staged_docx_markdown_roundtrip,
    compare_markdown_pair,
)

requires_docx_roundtrip = pytest.mark.skipif(
    shutil.which("pandoc") is None or importlib.util.find_spec("PIL") is None,
    reason="pandoc and pillow are required",
)


def _write_book_md(path: Path, *, body: str, translation_extra: str = "") -> None:
    fm = {
        "kind": "book",
        "number": 1,
        "slug": "01-test",
        "title": "Test",
        "lang": "en",
        "description": "A test book.",
        "tags": [],
        "cover": None,
        "translation": {"source": "ai"},
    }
    path.write_text(
        dump_frontmatter(fm).replace("source: ai\n", f"source: ai\n{translation_extra}") + body,
        encoding="utf-8",
    )


def _book_markdown(body: str) -> str:
    return f"""---
kind: book
number: 1
slug: 01-test
title: Test
lang: en
description: A test book.
tags: []
cover: null
translation:
  source: ai
---

{body}
"""


def _make_docx(tmp_path: Path, markdown: str) -> Path:
    md = tmp_path / "source.md"
    docx = tmp_path / "source.docx"
    md.write_text(markdown, encoding="utf-8")
    subprocess.run(["pandoc", str(md), "-o", str(docx)], check=True, capture_output=True, text=True)
    return docx


@requires_docx_roundtrip
def test_roundtrip_imports_into_temp_root_without_mutating_content(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    book_dir = content_root / "books" / "01-test"
    book_dir.mkdir(parents=True)
    committed_body = "Light.\n"
    _write_book_md(book_dir / "en.md", body=committed_body)
    docx = _make_docx(tmp_path, "Light.\n")
    shutil.copyfile(docx, book_dir / "en.docx")
    before = (book_dir / "en.md").read_text(encoding="utf-8")

    batch = check_docx_markdown_roundtrip(content_root=content_root, lang="en", book=1)

    assert batch.checked == 1
    assert batch.failed_count == 0
    assert (book_dir / "en.md").read_text(encoding="utf-8") == before


@requires_docx_roundtrip
def test_staged_roundtrip_checks_docx_before_commit(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    book_dir = content_root / "books" / "01-test"
    book_dir.mkdir(parents=True)
    committed_body = "Light.\n"
    _write_book_md(book_dir / "en.md", body=committed_body)
    staged_docx = _make_docx(tmp_path, "Light.\n")
    before = (book_dir / "en.md").read_text(encoding="utf-8")
    entry = next(entry for entry in scan_catalog(content_root) if entry.kind == "book")

    report = check_staged_docx_markdown_roundtrip(
        content_root=content_root,
        entry=entry,
        md_path=book_dir / "en.md",
        docx_path=staged_docx,
        lang="en",
    )

    assert not report.failed
    assert (book_dir / "en.md").read_text(encoding="utf-8") == before


def test_staged_roundtrip_refuses_non_book_entry(tmp_path: Path) -> None:
    entry = CatalogEntry(
        kind="poem",
        number=1,
        slug="test",
        title="Test",
        lang="en",
        description="",
        work_key="01-test",
        work_dir=tmp_path,
        md_path=tmp_path / "en.md",
        frontmatter={},
    )

    with pytest.raises(DocxRoundTripError, match="book entries"):
        check_staged_docx_markdown_roundtrip(
            content_root=tmp_path,
            entry=entry,
            md_path=entry.md_path,
            docx_path=tmp_path / "en.docx",
            lang="en",
        )


def test_roundtrip_reports_translated_docx_without_markdown(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    book_dir = content_root / "books" / "01-test"
    book_dir.mkdir(parents=True)
    (book_dir / "en.docx").write_bytes(b"not imported; only discovery is tested")

    batch = check_docx_markdown_roundtrip(content_root=content_root, lang="en")

    assert batch.checked == 0
    assert batch.missing_md == 1
    assert batch.failed


def test_roundtrip_reports_register_artifact_errors_as_fatal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from pancratius import import_docx
    from pancratius.intent_inference.errors import RegisterArtifactError

    content_root = tmp_path / "content"
    book_dir = content_root / "books" / "01-test"
    book_dir.mkdir(parents=True)
    _write_book_md(book_dir / "en.md", body="Light.\n")
    (book_dir / "en.docx").write_bytes(b"not imported; import_work is stubbed")

    def boom(_request: import_docx.ImportRequest) -> object:
        raise RegisterArtifactError("artifact weights sha256 mismatch")

    monkeypatch.setattr(import_docx, "import_work", boom)

    batch = check_docx_markdown_roundtrip(content_root=content_root, lang="en")

    assert batch.checked == 1
    assert batch.failed
    (report,) = batch.reports
    assert len(report.findings) == 1
    assert report.findings[0].severity == "fatal"
    assert report.findings[0].code == "roundtrip.artifact-contract"
    assert "weights sha256 mismatch" in report.findings[0].message


@requires_docx_roundtrip
def test_compare_tolerates_bootstrap_metadata_loss_but_flags_visible_text() -> None:
    committed = """---
kind: book
number: 1
slug: 01-test
title: Test
lang: en
description: A test book.
tags: []
cover: null
translation:
  source: ai
  model: test-model
  generated_at: 2026-06-23
---

Light.
"""
    imported_ok = """---
kind: book
number: 1
slug: 01-test
title: Test
lang: en
description: A test book.
tags: []
cover: null
translation:
  source: ai
---

Light.
"""
    imported_bad = imported_ok.replace("Light.", "Dark.")

    ok_findings = compare_markdown_pair(committed, imported_ok, lang="en")
    bad_findings = compare_markdown_pair(committed, imported_bad, lang="en")

    assert not any(finding.severity == "fatal" for finding in ok_findings)
    assert any(finding.code == "roundtrip.ignored-frontmatter-drift" for finding in ok_findings)
    assert any(finding.code == "roundtrip.visible-text-drift" for finding in bad_findings)


@requires_docx_roundtrip
def test_compare_treats_quote_style_as_typography_drift() -> None:
    committed = """---
kind: book
number: 1
slug: 01-test
title: Test
lang: en
description: A test book.
tags: []
cover: null
translation:
  source: ai
---

He said: «I am here».[1]
"""
    imported = committed.replace("«I am here».", "“I am here”.").replace(" .[1]", ".[1]")

    findings = compare_markdown_pair(committed, imported, lang="en")

    assert not any(finding.severity == "fatal" for finding in findings)
    assert any(finding.code == "roundtrip.typography-drift" for finding in findings)


@requires_docx_roundtrip
def test_compare_treats_ellipsis_style_as_typography_drift() -> None:
    committed = _book_markdown("This is a hint...\n")
    imported = _book_markdown("This is a hint…\n")

    findings = compare_markdown_pair(committed, imported, lang="en")

    assert not any(finding.severity == "fatal" for finding in findings)
    assert any(finding.code == "roundtrip.typography-drift" for finding in findings)


@requires_docx_roundtrip
def test_compare_fails_when_image_reference_changes() -> None:
    committed = _book_markdown("![Illustration](./images/a.jpg)\n")
    imported = _book_markdown("![Illustration](./images/b.jpg)\n")

    findings = compare_markdown_pair(committed, imported, lang="en")

    drift = next(finding for finding in findings if finding.code == "roundtrip.image-reference-drift")
    assert drift.severity == "fatal"


@requires_docx_roundtrip
def test_compare_accepts_rehashed_image_reference_with_same_payload(tmp_path: Path) -> None:
    committed_dir = tmp_path / "committed"
    imported_dir = tmp_path / "imported"
    committed_images = committed_dir / "images"
    imported_images = imported_dir / "images"
    committed_images.mkdir(parents=True)
    imported_images.mkdir(parents=True)
    payload = b"same image bytes"
    (committed_images / "pixel.png").write_bytes(payload)
    (imported_images / "6019c3c9e47d.png").write_bytes(payload)
    committed = _book_markdown("![Corpus diagram](./images/pixel.png)\n")
    imported = _book_markdown("![Corpus diagram](./images/6019c3c9e47d.png)\n")

    findings = compare_markdown_pair(
        committed,
        imported,
        lang="en",
        committed_dir=committed_dir,
        imported_dir=imported_dir,
    )

    assert not any(finding.code == "roundtrip.image-reference-drift" for finding in findings)


@requires_docx_roundtrip
def test_compare_treats_signature_html_text_as_visible() -> None:
    committed = _book_markdown("""I testify.

<p class="signature">
Pancratius
March 21, 2026
</p>

P.S. Continue.
""")
    imported = _book_markdown("I testify.\n\nPancratius March 21, 2026\n\nP.S. Continue.\n")

    findings = compare_markdown_pair(committed, imported, lang="en")

    assert not any(finding.severity == "fatal" for finding in findings)


@requires_docx_roundtrip
def test_compare_fails_when_lineation_structure_is_lost() -> None:
    committed = _book_markdown("""<div class="lineated">

First line
Second line

Third line

</div>
""")
    imported = _book_markdown("First line Second line Third line\n")

    findings = compare_markdown_pair(committed, imported, lang="en")

    drift = next(
        finding for finding in findings
        if finding.code == "roundtrip.lineation-structure-drift"
    )
    assert drift.severity == "fatal"


@requires_docx_roundtrip
def test_visible_text_drift_message_skips_tolerated_footnote_spacing() -> None:
    committed = _book_markdown("Creator, I was at the Liturgy[1], and then the light changed.\n")
    imported = _book_markdown("Creator, I was at the Liturgy [1], and then the word changed.\n")

    findings = compare_markdown_pair(committed, imported, lang="en")

    drift = next(finding for finding in findings if finding.code == "roundtrip.visible-text-drift")
    assert drift.severity == "fatal"
    assert "light changed" in drift.message
    assert "Liturgy [1]" not in drift.message
