from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "audit" / "source_text_fidelity.py"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
OFFICE_DOCUMENT_REL = f"{OFFICE_REL_NS}/officeDocument"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _content_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src" / "content" / "books" / "sample").mkdir(parents=True)
    return root


def _write_docx(path: Path, text: str) -> None:
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
        "word/document.xml": (
            f'<w:document xmlns:w="{W_NS}" xmlns:r="{OFFICE_REL_NS}">'
            f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p><w:sectPr /></w:body>"
            "</w:document>"
        ).encode(),
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, payload in parts.items():
            zf.writestr(name, payload)


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


def test_source_text_fidelity_rejects_exactly_duplicated_markdown_body(tmp_path: Path) -> None:
    root = _content_root(tmp_path)
    words = " ".join(f"word{i}" for i in range(40))
    docx = root / "src" / "content" / "books" / "sample" / "ru.docx"
    _write_docx(docx, words)
    docx.with_suffix(".md").write_text(f"---\ntitle: Sample\n---\n\n{words}\n{words}\n", encoding="utf-8")

    result = _run_audit(root)

    assert result.returncode == 1
    assert "Markdown body text appears duplicated exactly" in result.stderr


def test_source_text_fidelity_rejects_partially_duplicated_markdown_passage(tmp_path: Path) -> None:
    root = _content_root(tmp_path)
    shared = " ".join(f"shared{i}" for i in range(35))
    tail = " ".join(f"tail{i}" for i in range(35))
    docx = root / "src" / "content" / "books" / "sample" / "ru.docx"
    _write_docx(docx, f"{shared} {tail}")
    docx.with_suffix(".md").write_text(
        f"---\ntitle: Sample\n---\n\n{shared} {tail}\n\n{shared}\n",
        encoding="utf-8",
    )

    result = _run_audit(root)

    assert result.returncode == 1
    assert "Markdown repeats a source passage more often than DOCX" in result.stderr
