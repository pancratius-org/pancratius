from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path

from pancratius.ooxml import R_NS, REL_NS, W_NS
from pancratius.translation.docx.audit import audit_translated_docx_artifacts

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "audit" / "python" / "translated_docx_transfer.py"
OFFICE_DOCUMENT_REL = f"{R_NS}/officeDocument"


def _content_root(tmp_path: Path) -> Path:
    return tmp_path / "repo"


def _docx_path(root: Path, *, collection: str = "books", work: str = "sample") -> Path:
    work_dir = root / "src" / "content" / collection / work
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir / "en.docx"


def _write_docx(
    root: Path,
    *,
    collection: str = "books",
    work: str = "sample",
    document_extra: str = "",
    document_relationships: str = "",
    footnotes_xml: str | None = None,
    footnote_relationships: str = "",
) -> Path:
    docx = _docx_path(root, collection=collection, work=work)
    document_xml = (
        f'<w:document xmlns:w="{W_NS}" xmlns:r="{R_NS}">'
        "<w:body>"
        "<w:p><w:r><w:t>Light</w:t></w:r></w:p>"
        f"{document_extra}"
        "<w:sectPr />"
        "</w:body>"
        "</w:document>"
    ).encode()
    parts = {
        "[Content_Types].xml": (
            b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            b'<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            b'<Default Extension="xml" ContentType="application/xml"/>'
            b'<Override PartName="/word/document.xml" '
            b'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            b"</Types>"
        ),
        "_rels/.rels": (
            f'<Relationships xmlns="{REL_NS}">'
            f'<Relationship Id="rId1" Type="{OFFICE_DOCUMENT_REL}" Target="word/document.xml"/>'
            "</Relationships>"
        ).encode(),
        "word/document.xml": document_xml,
        "word/_rels/document.xml.rels": (
            f'<Relationships xmlns="{REL_NS}">{document_relationships}</Relationships>'
        ).encode(),
    }
    if footnotes_xml is not None:
        parts["word/footnotes.xml"] = footnotes_xml.encode()
        parts["word/_rels/footnotes.xml.rels"] = (
            f'<Relationships xmlns="{REL_NS}">{footnote_relationships}</Relationships>'
        ).encode()
    with zipfile.ZipFile(docx, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, payload in parts.items():
            zf.writestr(name, payload)
    return docx


def _write_zip_without_document_xml(root: Path) -> Path:
    docx = _docx_path(root)
    with zipfile.ZipFile(docx, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types" />',
        )
    return docx


def _write_docx_with_malformed_document_xml(root: Path) -> Path:
    docx = _docx_path(root)
    with zipfile.ZipFile(docx, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types" />',
        )
        zf.writestr("word/document.xml", b"<w:document")
    return docx


def _run_audit(root: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PANCRATIUS_AUDIT_ROOT": str(root)}
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        check=False,
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
    )


def test_translated_docx_transfer_accepts_clean_translated_docx(tmp_path: Path) -> None:
    root = _content_root(tmp_path)
    _write_docx(root)

    result = _run_audit(root)

    assert result.returncode == 0, result.stderr


def test_translated_docx_transfer_package_audit_reports_checked_artifacts(tmp_path: Path) -> None:
    root = _content_root(tmp_path)
    _write_docx(root)
    _write_docx(root, collection="poetry", work="verse")

    report = audit_translated_docx_artifacts(root)

    assert report.checked == 2
    assert not report.failed
    assert not report.issues


def test_translated_docx_transfer_package_audit_reports_failures(tmp_path: Path) -> None:
    root = _content_root(tmp_path)
    docx = _write_docx(
        root,
        document_extra='<w:p><w:r><w:footnoteReference w:id="1" /></w:r></w:p>',
        footnotes_xml=(
            f'<w:footnotes xmlns:w="{W_NS}">'
            '<w:footnote w:id="2"><w:p><w:r><w:t>Note</w:t></w:r></w:p></w:footnote>'
            "</w:footnotes>"
        ),
    )

    report = audit_translated_docx_artifacts(root)

    assert report.failed
    assert len(report.issues) == 1
    assert report.issues[0].path == docx
    assert "body footnote reference ids [1]" in report.issues[0].message
    assert "positive footnote definition ids [2]" in report.issues[0].message


def test_translated_docx_transfer_accepts_matching_footnote_table(tmp_path: Path) -> None:
    root = _content_root(tmp_path)
    _write_docx(
        root,
        document_extra='<w:p><w:r><w:footnoteReference w:id="1" /></w:r></w:p>',
        footnotes_xml=(
            f'<w:footnotes xmlns:w="{W_NS}">'
            '<w:footnote w:id="-1"><w:p /></w:footnote>'
            '<w:footnote w:id="0"><w:p /></w:footnote>'
            '<w:footnote w:id="1"><w:p><w:r><w:t>Note</w:t></w:r></w:p></w:footnote>'
            "</w:footnotes>"
        ),
    )

    result = _run_audit(root)

    assert result.returncode == 0, result.stderr


def test_translated_docx_transfer_rejects_cyrillic_drawing_metadata(tmp_path: Path) -> None:
    root = _content_root(tmp_path)
    _write_docx(
        root,
        document_extra=(
            '<w:p><w:r><w:drawing>'
            '<wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">'
            '<wp:docPr id="1" name="Рисунок 1" descr="Иллюстрация" />'
            '<wp:cNvGraphicFramePr />'
            "</wp:inline>"
            "</w:drawing></w:r></w:p>"
        ),
    )

    result = _run_audit(root)

    assert result.returncode == 1
    assert "word/document.xml docPr@name contains Cyrillic text 'Рисунок 1'" in result.stderr
    assert "word/document.xml docPr@descr contains Cyrillic text 'Иллюстрация'" in result.stderr


def test_translated_docx_transfer_scans_poetry_translations(tmp_path: Path) -> None:
    root = _content_root(tmp_path)
    _write_docx(
        root,
        collection="poetry",
        work="verse",
        document_extra='<w:p><w:r><w:footnoteReference w:id="1" /></w:r></w:p>',
    )

    result = _run_audit(root)

    assert result.returncode == 1
    assert "src/content/poetry/verse/en.docx" in result.stderr


def test_translated_docx_transfer_ignores_russian_source_docx(tmp_path: Path) -> None:
    root = _content_root(tmp_path)
    _write_docx(root)
    _docx_path(root).with_name("ru.docx").write_bytes(b"not a zip")

    result = _run_audit(root)

    assert result.returncode == 0, result.stderr


def test_translated_docx_transfer_rejects_unreadable_docx(tmp_path: Path) -> None:
    root = _content_root(tmp_path)
    docx = _docx_path(root)
    docx.write_bytes(b"not a zip")

    result = _run_audit(root)

    assert result.returncode == 1
    assert "not a valid ZIP/DOCX package" in result.stderr


def test_translated_docx_transfer_rejects_zip_without_document_xml(tmp_path: Path) -> None:
    root = _content_root(tmp_path)
    _write_zip_without_document_xml(root)

    result = _run_audit(root)

    assert result.returncode == 1
    assert "missing required DOCX part: word/document.xml" in result.stderr


def test_translated_docx_transfer_rejects_malformed_document_xml(tmp_path: Path) -> None:
    root = _content_root(tmp_path)
    _write_docx_with_malformed_document_xml(root)

    result = _run_audit(root)

    assert result.returncode == 1
    assert "word/document.xml is not well-formed XML" in result.stderr


def test_translated_docx_transfer_rejects_malformed_footnotes_xml(tmp_path: Path) -> None:
    root = _content_root(tmp_path)
    _write_docx(
        root,
        document_extra='<w:p><w:r><w:footnoteReference w:id="1" /></w:r></w:p>',
        footnotes_xml="<w:footnotes",
    )

    result = _run_audit(root)

    assert result.returncode == 1
    assert "word/footnotes.xml is not well-formed XML" in result.stderr


def test_translated_docx_transfer_rejects_missing_footnote_part(tmp_path: Path) -> None:
    root = _content_root(tmp_path)
    _write_docx(
        root,
        document_extra='<w:p><w:r><w:footnoteReference w:id="1" /></w:r></w:p>',
    )

    result = _run_audit(root)

    assert result.returncode == 1
    assert "word/document.xml has footnote references but word/footnotes.xml is missing" in result.stderr


def test_translated_docx_transfer_rejects_missing_footnote_definition(
    tmp_path: Path,
) -> None:
    root = _content_root(tmp_path)
    _write_docx(
        root,
        document_extra=(
            '<w:p><w:r><w:footnoteReference w:id="1" /></w:r></w:p>'
            '<w:p><w:r><w:footnoteReference w:id="2" /></w:r></w:p>'
        ),
        footnotes_xml=(
            f'<w:footnotes xmlns:w="{W_NS}">'
            '<w:footnote w:id="1"><w:p><w:r><w:t>Note</w:t></w:r></w:p></w:footnote>'
            "</w:footnotes>"
        ),
    )

    result = _run_audit(root)

    assert result.returncode == 1
    assert "body footnote reference ids [1, 2]" in result.stderr
    assert "positive footnote definition ids [1]" in result.stderr


def test_translated_docx_transfer_rejects_orphan_footnote_definition(
    tmp_path: Path,
) -> None:
    root = _content_root(tmp_path)
    _write_docx(
        root,
        document_extra='<w:p><w:r><w:footnoteReference w:id="1" /></w:r></w:p>',
        footnotes_xml=(
            f'<w:footnotes xmlns:w="{W_NS}">'
            '<w:footnote w:id="1"><w:p><w:r><w:t>Note</w:t></w:r></w:p></w:footnote>'
            '<w:footnote w:id="2"><w:p><w:r><w:t>Orphan</w:t></w:r></w:p></w:footnote>'
            "</w:footnotes>"
        ),
    )

    result = _run_audit(root)

    assert result.returncode == 1
    assert "body footnote reference ids [1]" in result.stderr
    assert "positive footnote definition ids [1, 2]" in result.stderr


def test_translated_docx_transfer_accepts_reserved_definitions_without_body_refs(
    tmp_path: Path,
) -> None:
    root = _content_root(tmp_path)
    _write_docx(
        root,
        footnotes_xml=(
            f'<w:footnotes xmlns:w="{W_NS}">'
            '<w:footnote w:id="-1"><w:p /></w:footnote>'
            '<w:footnote w:id="0"><w:p /></w:footnote>'
            "</w:footnotes>"
        ),
    )

    result = _run_audit(root)

    assert result.returncode == 0, result.stderr


def test_translated_docx_transfer_rejects_positive_definition_without_body_ref(
    tmp_path: Path,
) -> None:
    root = _content_root(tmp_path)
    _write_docx(
        root,
        footnotes_xml=(
            f'<w:footnotes xmlns:w="{W_NS}">'
            '<w:footnote w:id="1"><w:p /></w:footnote>'
            "</w:footnotes>"
        ),
    )

    result = _run_audit(root)

    assert result.returncode == 1
    assert "body footnote reference ids []" in result.stderr
    assert "positive footnote definition ids [1]" in result.stderr


def test_translated_docx_transfer_rejects_malformed_footnote_ids(tmp_path: Path) -> None:
    root = _content_root(tmp_path)
    _write_docx(
        root,
        document_extra=(
            "<w:p><w:r><w:footnoteReference /></w:r></w:p>"
            '<w:p><w:r><w:footnoteReference w:id="abc" /></w:r></w:p>'
            '<w:p><w:r><w:footnoteReference w:id="0" /></w:r></w:p>'
            '<w:p><w:r><w:footnoteReference w:id="1" /></w:r></w:p>'
        ),
        footnotes_xml=(
            f'<w:footnotes xmlns:w="{W_NS}">'
            "<w:footnote><w:p /></w:footnote>"
            '<w:footnote w:id="abc"><w:p /></w:footnote>'
            '<w:footnote w:id="1"><w:p /></w:footnote>'
            "</w:footnotes>"
        ),
    )

    result = _run_audit(root)

    assert result.returncode == 1
    assert "word/document.xml footnoteReference is missing w:id" in result.stderr
    assert "word/document.xml footnoteReference has non-integer w:id 'abc'" in result.stderr
    assert "word/document.xml footnoteReference uses non-positive w:id 0" in result.stderr
    assert "word/footnotes.xml footnote is missing w:id" in result.stderr
    assert "word/footnotes.xml footnote has non-integer w:id 'abc'" in result.stderr


def test_translated_docx_transfer_rejects_duplicate_positive_definitions(
    tmp_path: Path,
) -> None:
    root = _content_root(tmp_path)
    _write_docx(
        root,
        document_extra='<w:p><w:r><w:footnoteReference w:id="1" /></w:r></w:p>',
        footnotes_xml=(
            f'<w:footnotes xmlns:w="{W_NS}">'
            '<w:footnote w:id="1"><w:p /></w:footnote>'
            '<w:footnote w:id="1"><w:p /></w:footnote>'
            "</w:footnotes>"
        ),
    )

    result = _run_audit(root)

    assert result.returncode == 1
    assert "duplicate positive footnote definition ids [1]" in result.stderr


def test_translated_docx_transfer_rejects_duplicate_positive_references(
    tmp_path: Path,
) -> None:
    root = _content_root(tmp_path)
    _write_docx(
        root,
        document_extra=(
            '<w:p><w:r><w:footnoteReference w:id="1" /></w:r></w:p>'
            '<w:p><w:r><w:footnoteReference w:id="1" /></w:r></w:p>'
        ),
        footnotes_xml=(
            f'<w:footnotes xmlns:w="{W_NS}">'
            '<w:footnote w:id="1"><w:p /></w:footnote>'
            "</w:footnotes>"
        ),
    )

    result = _run_audit(root)

    assert result.returncode == 1
    assert "duplicate positive footnote reference ids [1]" in result.stderr
