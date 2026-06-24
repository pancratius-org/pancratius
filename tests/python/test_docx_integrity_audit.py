from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "audit" / "docx_integrity.py"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
OFFICE_DOCUMENT_REL = f"{OFFICE_REL_NS}/officeDocument"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _content_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src" / "content" / "books" / "sample").mkdir(parents=True)
    return root


def _paragraph(text: str) -> str:
    return f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"


def _write_docx(
    root: Path,
    *,
    paragraphs: list[str] | None = None,
    root_target: str = "word/document.xml",
    document_extra: str = "",
    document_relationships: str = "",
    include_document_content_type: bool = True,
    xml_default_content_type: str = "application/xml",
    png_default_content_type: str = "image/png",
    gif_default_content_type: str = "image/gif",
    extra_parts: dict[str, bytes] | None = None,
) -> Path:
    docx = root / "src" / "content" / "books" / "sample" / "ru.docx"
    body = "".join(_paragraph(text) for text in (paragraphs or ["One", "Two"]))
    document_xml = (
        f'<w:document xmlns:w="{W_NS}" xmlns:r="{OFFICE_REL_NS}">'
        f"<w:body>{body}{document_extra}<w:sectPr /></w:body>"
        "</w:document>"
    ).encode()
    document_override = (
        b'<Override PartName="/word/document.xml" '
        b'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        if include_document_content_type
        else b""
    )
    parts = {
        "[Content_Types].xml": (
            b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            b'<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            + f'<Default Extension="xml" ContentType="{xml_default_content_type}"/>'.encode()
            + f'<Default Extension="png" ContentType="{png_default_content_type}"/>'.encode()
            + f'<Default Extension="gif" ContentType="{gif_default_content_type}"/>'.encode()
            + document_override
            + b"</Types>"
        ),
        "_rels/.rels": (
            f'<Relationships xmlns="{REL_NS}">'
            f'<Relationship Id="rId1" Type="{OFFICE_DOCUMENT_REL}" Target="{root_target}"/>'
            "</Relationships>"
        ).encode(),
        "word/document.xml": document_xml,
        "word/_rels/document.xml.rels": (
            f'<Relationships xmlns="{REL_NS}">{document_relationships}</Relationships>'
        ).encode(),
    }
    parts.update(extra_parts or {})
    with zipfile.ZipFile(docx, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, payload in parts.items():
            zf.writestr(name, payload)
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


def test_docx_integrity_accepts_valid_source_docx(tmp_path: Path) -> None:
    root = _content_root(tmp_path)
    _write_docx(root)

    result = _run_audit(root)

    assert result.returncode == 0, result.stderr


def test_docx_integrity_rejects_wrong_root_office_document_target(tmp_path: Path) -> None:
    root = _content_root(tmp_path)
    _write_docx(root, root_target="word/other.xml", extra_parts={"word/other.xml": b"<unused />"})

    result = _run_audit(root)

    assert result.returncode == 1
    assert "root officeDocument relationship must point to word/document.xml" in result.stderr


def test_docx_integrity_rejects_duplicate_zip_part_names(tmp_path: Path) -> None:
    root = _content_root(tmp_path)
    docx = _write_docx(root)
    with (
        zipfile.ZipFile(docx, "a", compression=zipfile.ZIP_DEFLATED) as zf,
        pytest.warns(UserWarning, match="Duplicate name"),
    ):
        zf.writestr("word/document.xml", b"<duplicate-document />")

    result = _run_audit(root)

    assert result.returncode == 1
    assert "duplicate ZIP part name(s): word/document.xml" in result.stderr


def test_docx_integrity_rejects_missing_document_content_type(tmp_path: Path) -> None:
    root = _content_root(tmp_path)
    _write_docx(root, include_document_content_type=False)

    result = _run_audit(root)

    assert result.returncode == 1
    assert "word/document.xml has no main-document content type" in result.stderr


def test_docx_integrity_accepts_main_document_content_type_via_default(tmp_path: Path) -> None:
    root = _content_root(tmp_path)
    _write_docx(
        root,
        include_document_content_type=False,
        xml_default_content_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
        ),
    )

    result = _run_audit(root)

    assert result.returncode == 0, result.stderr


def test_docx_integrity_rejects_wrong_media_content_type(tmp_path: Path) -> None:
    root = _content_root(tmp_path)
    _write_docx(
        root,
        png_default_content_type="text/plain",
        extra_parts={"word/media/image1.png": b"not-really-important"},
    )

    result = _run_audit(root)

    assert result.returncode == 1
    assert "word/media/image1.png has content type 'text/plain'" in result.stderr


def test_docx_integrity_rejects_wrong_non_png_media_content_type(tmp_path: Path) -> None:
    root = _content_root(tmp_path)
    _write_docx(
        root,
        gif_default_content_type="text/plain",
        extra_parts={"word/media/image1.gif": b"not-really-important"},
    )

    result = _run_audit(root)

    assert result.returncode == 1
    assert "word/media/image1.gif has content type 'text/plain'" in result.stderr


def test_docx_integrity_rejects_single_quoted_missing_relationship_ref(tmp_path: Path) -> None:
    root = _content_root(tmp_path)
    _write_docx(root, document_extra="<w:pict r:embed='missingImage' />")

    result = _run_audit(root)

    assert result.returncode == 1
    assert "unresolved relationship reference(s): missingImage" in result.stderr


def test_docx_integrity_rejects_embed_pointing_to_external_relationship(tmp_path: Path) -> None:
    root = _content_root(tmp_path)
    _write_docx(
        root,
        document_extra='<w:pict r:embed="rIdExternal" />',
        document_relationships=(
            '<Relationship Id="rIdExternal" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
            'Target="https://example.test/image.png" TargetMode="External" />'
        ),
    )

    result = _run_audit(root)

    assert result.returncode == 1
    assert "r:embed=rIdExternal pointing to an external relationship" in result.stderr


def test_docx_integrity_rejects_external_target_without_target_mode(tmp_path: Path) -> None:
    root = _content_root(tmp_path)
    _write_docx(
        root,
        document_relationships=(
            '<Relationship Id="rIdExternal" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
            'Target="https://example.test/word/media/image1.png" />'
        ),
        extra_parts={"word/media/image1.png": b"image"},
    )

    result = _run_audit(root)

    assert result.returncode == 1
    assert "is external without TargetMode=External" in result.stderr


def test_docx_integrity_rejects_invalid_target_mode(tmp_path: Path) -> None:
    root = _content_root(tmp_path)
    _write_docx(
        root,
        document_relationships=(
            '<Relationship Id="rIdInvalidMode" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
            'Target="media/image1.png" TargetMode="external" />'
        ),
        extra_parts={"word/media/image1.png": b"image"},
    )

    result = _run_audit(root)

    assert result.returncode == 1
    assert "relationship rIdInvalidMode has invalid TargetMode 'external'" in result.stderr


def test_docx_integrity_rejects_embed_pointing_to_wrong_internal_type(tmp_path: Path) -> None:
    root = _content_root(tmp_path)
    _write_docx(
        root,
        document_extra='<w:pict r:embed="rIdHyperlink" />',
        document_relationships=(
            '<Relationship Id="rIdHyperlink" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
            'Target="media/image1.png" />'
        ),
        extra_parts={"word/media/image1.png": b"image"},
    )

    result = _run_audit(root)

    assert result.returncode == 1
    assert "r:embed=rIdHyperlink pointing to non-embeddable relationship type" in result.stderr


def test_docx_integrity_rejects_orphan_relationship_part(tmp_path: Path) -> None:
    root = _content_root(tmp_path)
    _write_docx(
        root,
        extra_parts={
            "word/_rels/missing.xml.rels": f'<Relationships xmlns="{REL_NS}" />'.encode()
        },
    )

    result = _run_audit(root)

    assert result.returncode == 1
    assert "word/_rels/missing.xml.rels has no source part word/missing.xml" in result.stderr


def test_docx_integrity_accepts_root_level_relationship_part(tmp_path: Path) -> None:
    root = _content_root(tmp_path)
    _write_docx(
        root,
        extra_parts={
            "custom.xml": b"<custom />",
            "_rels/custom.xml.rels": f'<Relationships xmlns="{REL_NS}" />'.encode(),
        },
    )

    result = _run_audit(root)

    assert result.returncode == 0, result.stderr


def test_docx_integrity_rejects_misplaced_nested_relationship_part(tmp_path: Path) -> None:
    root = _content_root(tmp_path)
    _write_docx(
        root,
        extra_parts={
            "word/charts/chart1.xml": b"<chart />",
            "word/media/image1.png": b"image",
            "word/_rels/charts/chart1.xml.rels": (
                f'<Relationships xmlns="{REL_NS}">'
                '<Relationship Id="rIdImage" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
                'Target="../media/image1.png" />'
                "</Relationships>"
            ).encode(),
        },
    )

    result = _run_audit(root)

    assert result.returncode == 1
    assert "unexpected relationships part path: word/_rels/charts/chart1.xml.rels" in result.stderr


def test_docx_integrity_rejects_exact_parent_package_escape(tmp_path: Path) -> None:
    root = _content_root(tmp_path)
    _write_docx(
        root,
        document_relationships=(
            '<Relationship Id="rIdEscape" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
            'Target="../.." />'
        ),
        extra_parts={"..": b"not a package part"},
    )

    result = _run_audit(root)

    assert result.returncode == 1
    assert "escapes the DOCX package" in result.stderr


def test_docx_integrity_rejects_missing_header_relationship_ref(tmp_path: Path) -> None:
    root = _content_root(tmp_path)
    _write_docx(
        root,
        extra_parts={
            "word/header1.xml": (
                f'<w:hdr xmlns:w="{W_NS}" xmlns:r="{OFFICE_REL_NS}">'
                "<w:p><w:pict r:embed=\"missingHeaderImage\" /></w:p>"
                "</w:hdr>"
            ).encode(),
        },
    )

    result = _run_audit(root)

    assert result.returncode == 1
    assert "word/header1.xml has unresolved relationship reference(s): missingHeaderImage" in result.stderr


def test_docx_integrity_rejects_short_exact_duplicate_body_halves(tmp_path: Path) -> None:
    root = _content_root(tmp_path)
    _write_docx(root, paragraphs=["A", "B", "C", "A", "B", "C"])

    result = _run_audit(root)

    assert result.returncode == 1
    assert "body text appears duplicated exactly" in result.stderr


def test_docx_integrity_rejects_duplicate_media_payloads(tmp_path: Path) -> None:
    root = _content_root(tmp_path)
    _write_docx(
        root,
        extra_parts={
            "word/media/image1.png": b"same-image-bytes",
            "word/media/image2.png": b"same-image-bytes",
        },
    )

    result = _run_audit(root)

    assert result.returncode == 1
    assert "duplicate media payload(s)" in result.stderr
