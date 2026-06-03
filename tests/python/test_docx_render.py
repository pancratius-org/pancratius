from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pancratius import cli, docx_render
from pancratius.docx_inspect import ParaRow


def _row(text: str, *, index: int = 0) -> ParaRow:
    return ParaRow(
        index=index,
        text=text,
        style="Normal",
        direct_style="",
        align="",
        contextual=False,
        spacing={},
        indent={},
        numbered=False,
        bordered=False,
        heading=False,
        thematic=False,
        br_count=0,
        empty=False,
    )


def test_docx_render_slice_cli_renders_selected_range(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rows = [_row("Alpha")]
    out = tmp_path / "slice.png"

    def resolve_range_stub(
        docx: Path,
        *,
        around: str | None = None,
        context: int = 10,
        index_range: tuple[int, int] | None = None,
    ) -> tuple[int, int, list[ParaRow]]:
        assert docx == Path("source.docx")
        assert around is None
        assert context == 10
        assert index_range == (0, 0)
        return 0, 0, rows

    def render_stub(
        docx: Path,
        lo: int,
        hi: int,
        out_png: Path,
        *,
        dpi: int = 140,
    ) -> list[Path]:
        assert docx == Path("source.docx")
        assert (lo, hi) == (0, 0)
        assert out_png == out
        assert dpi == 140
        return [out]

    monkeypatch.setattr(docx_render, "resolve_range", resolve_range_stub)
    monkeypatch.setattr(docx_render, "render", render_stub)

    rc = cli.main(["docx", "render-slice", "source.docx", "--range", "0:0", "--out", str(out)])

    assert rc == 0
    stdout = capsys.readouterr().out
    assert "rendered paragraphs [0..0]" in stdout
    assert "Alpha" in stdout


def test_docx_render_slice_cli_reports_missing_source(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main([
        "docx",
        "render-slice",
        "/tmp/pancratius-missing.docx",
        "--range",
        "0:0",
        "--out",
        "/tmp/pancratius-slice.png",
    ])

    assert rc == 2
    assert "DOCX not found" in capsys.readouterr().err


def test_docx_render_uses_isolated_libreoffice_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.docx"
    source.write_bytes(b"not read by this test")
    out = tmp_path / "slice.png"
    commands: list[list[str]] = []

    def slice_docx_stub(docx: Path, lo: int, hi: int, dest: Path) -> Path:
        assert docx == source
        assert (lo, hi) == (2, 4)
        dest.write_bytes(b"docx")
        return dest

    def run_stub(
        cmd: list[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert check
        assert capture_output
        assert text
        commands.append(cmd)
        if cmd[0] == "soffice":
            assert any(arg.startswith("-env:UserInstallation=file://") for arg in cmd)
            outdir = Path(cmd[cmd.index("--outdir") + 1])
            (outdir / "slice.pdf").write_bytes(b"pdf")
        elif cmd[0] == "pdftoppm":
            Path(f"{cmd[-1]}-1.png").write_bytes(b"png")
        else:  # pragma: no cover - guards test fixture calls
            raise AssertionError(f"unexpected command: {cmd}")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(docx_render, "_soffice", lambda: "soffice")
    monkeypatch.setattr(docx_render, "slice_docx", slice_docx_stub)
    monkeypatch.setattr(docx_render.subprocess, "run", run_stub)

    pages = docx_render.render(source, 2, 4, out)

    assert pages == [tmp_path / "slice-1.png"]
    assert [cmd[0] for cmd in commands] == ["soffice", "pdftoppm"]


def test_docx_render_around_requires_unique_match(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.docx"
    source.write_bytes(b"not read by this test")
    rows = [
        _row("Alpha", index=0),
        _row("Needle one", index=1),
        _row("Needle two", index=2),
    ]
    monkeypatch.setattr(docx_render.di, "read_rows", lambda _docx: rows)

    with pytest.raises(docx_render.DocxRenderError, match="matched 2 paragraphs"):
        docx_render.resolve_range(source, around="Needle")


def test_docx_render_reports_libreoffice_output_when_no_pdf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.docx"
    source.write_bytes(b"not read by this test")

    def slice_docx_stub(_docx: Path, _lo: int, _hi: int, dest: Path) -> Path:
        dest.write_bytes(b"docx")
        return dest

    def run_stub(
        cmd: list[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        if cmd[0] == "soffice":
            return subprocess.CompletedProcess(cmd, 0, "", "Error: source file could not be loaded")
        raise AssertionError(f"unexpected command: {cmd}")  # pragma: no cover

    monkeypatch.setattr(docx_render, "_soffice", lambda: "soffice")
    monkeypatch.setattr(docx_render, "slice_docx", slice_docx_stub)
    monkeypatch.setattr(docx_render.subprocess, "run", run_stub)

    with pytest.raises(docx_render.DocxRenderError, match="source file could not be loaded"):
        docx_render.render(source, 0, 0, tmp_path / "slice.png")
