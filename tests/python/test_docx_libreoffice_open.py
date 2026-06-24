from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import pancratius.docx_libreoffice_open as lo_open


def test_libreoffice_open_uses_scratch_copy_and_isolated_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content_root = tmp_path / "src" / "content"
    docx = content_root / "books" / "01-sample" / "en.docx"
    docx.parent.mkdir(parents=True)
    docx.write_bytes(b"docx")
    out_dir = tmp_path / "pdf"
    stale_pdf = out_dir / "books" / "01-sample" / "en.pdf"
    stale_pdf.parent.mkdir(parents=True)
    stale_pdf.write_bytes(b"stale")
    seen: list[list[str]] = []

    def run_stub(
        cmd: list[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        assert not check
        assert capture_output
        assert text
        assert timeout == lo_open.PDF_RENDER_TIMEOUT_SECONDS
        seen.append(cmd)
        source = Path(cmd[-1])
        assert source.name == "en.docx"
        assert source.parent != docx.parent
        assert any(arg.startswith("-env:UserInstallation=file://") for arg in cmd)
        output_dir = Path(cmd[cmd.index("--outdir") + 1])
        assert not (output_dir / "en.pdf").exists()
        (output_dir / "en.pdf").write_bytes(b"pdf")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(lo_open, "_find_soffice", lambda: "soffice")
    monkeypatch.setattr(lo_open.subprocess, "run", run_stub)

    results = lo_open.run_check(content_root=content_root, out_dir=out_dir)

    assert len(results) == 1
    assert results[0].ok
    assert results[0].pdf == out_dir / "books" / "01-sample" / "en.pdf"
    assert len(seen) == 1


def test_libreoffice_open_fails_when_pdf_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content_root = tmp_path / "src" / "content"
    docx = content_root / "books" / "01-sample" / "en.docx"
    docx.parent.mkdir(parents=True)
    docx.write_bytes(b"docx")

    def run_stub(
        cmd: list[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        assert not check
        assert capture_output
        assert text
        assert timeout == lo_open.PDF_RENDER_TIMEOUT_SECONDS
        return subprocess.CompletedProcess(cmd, 0, "converted", "")

    monkeypatch.setattr(lo_open, "_find_soffice", lambda: "soffice")
    monkeypatch.setattr(lo_open.subprocess, "run", run_stub)

    results = lo_open.run_check(content_root=content_root, out_dir=tmp_path / "pdf")

    assert len(results) == 1
    assert not results[0].ok
    assert "produced no PDF" in results[0].message


def test_libreoffice_open_omits_temp_pdf_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content_root = tmp_path / "src" / "content"
    docx = content_root / "books" / "01-sample" / "en.docx"
    docx.parent.mkdir(parents=True)
    docx.write_bytes(b"docx")

    def run_stub(
        cmd: list[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        del check, capture_output, text, timeout
        output_dir = Path(cmd[cmd.index("--outdir") + 1])
        (output_dir / "en.pdf").write_bytes(b"pdf")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(lo_open, "_find_soffice", lambda: "soffice")
    monkeypatch.setattr(lo_open.subprocess, "run", run_stub)

    results = lo_open.run_check(content_root=content_root)

    assert len(results) == 1
    assert results[0].ok
    assert results[0].pdf is None


def test_libreoffice_open_reports_missing_soffice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lo_open, "_find_soffice", lambda: None)

    rc = lo_open.main(["--content-root", str(tmp_path)])

    assert rc == 2


def test_libreoffice_open_fails_when_no_docx_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lo_open, "_find_soffice", lambda: "soffice")

    rc = lo_open.main(["--content-root", str(tmp_path)])

    assert rc == 2


def test_libreoffice_open_rejects_output_inside_content_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content_root = tmp_path / "src" / "content"
    docx = content_root / "books" / "01-sample" / "en.docx"
    docx.parent.mkdir(parents=True)
    docx.write_bytes(b"docx")
    monkeypatch.setattr(lo_open, "_find_soffice", lambda: "soffice")

    with pytest.raises(ValueError, match="--out-dir must not be inside"):
        lo_open.run_check(content_root=content_root, out_dir=content_root / "_qa")
