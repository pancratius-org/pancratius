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


# ---------------------------------------------------------------------------
# Fix A: typed converter diagnostics flow into the WritePlan and a FATAL blocks
# ---------------------------------------------------------------------------


def test_converter_fatal_diagnostic_blocks_the_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A converter-side FATAL (the unresolvable-local-image fatal proven in
    # test_ir_pipeline) must reach WritePlan.diagnostics and make the writer REFUSE —
    # nothing written. Today the converter flattened diagnostics into a warning STRING,
    # so a fatal could not block. We inject the fatal at the asset pass (its real
    # trigger) and assert the whole bundle is refused.
    from lib import ir, ir_lower

    real_assign = ir_lower.assign_assets

    def fatal_assign(doc: "ir.Document", media_root: Path, lang: str) -> list:
        out = real_assign(doc, media_root, lang)
        doc.diagnostics.append(ir.Diagnostic("fatal", "import.image-unresolved", "synthetic fatal"))
        return out

    monkeypatch.setattr(ir_lower, "assign_assets", fatal_assign)

    content_root = tmp_path / "src" / "content"
    parsed = import_docx.build_parser().parse_args([
        str(ROOT / "legacy/books/ru/23-личность-и-эго.docx"),
        "--kind", "book", "--lang", "ru", "--number", "91", "--slug", "fatal-probe",
        "--title", "Fatal Probe", "--description", "d.",
        "--out-content", str(content_root),
    ])
    with pytest.raises(SystemExit):
        import_docx.run(parsed)

    work_dir = content_root / "books" / "91-fatal-probe"
    assert not (work_dir / "ru.md").exists(), "a converter FATAL must block the write entirely"


def test_converter_typed_diagnostics_carry_severity(tmp_path: Path) -> None:
    # The converter must expose TYPED diagnostics (not just a flattened string), so a
    # downstream consumer can read severity. A clean import still carries any
    # info/warning diagnostics with their severity intact.
    from lib import content_catalog
    from lib.docx_conversion import convert_single_docx

    media = tmp_path / "media"
    converted = convert_single_docx(
        ROOT / "legacy/books/ru/23-личность-и-эго.docx",
        kind="book", lang="ru", work_key="91-probe", title="Probe",
        work_dir=tmp_path / "stage",
        title_index=content_catalog.build_title_index([]),
        media_out=media,
    )
    assert hasattr(converted, "diagnostics"), "ConvertedDocx must expose typed diagnostics"
    assert all(d.severity in {"fatal", "warning", "info"} for d in converted.diagnostics)
    # The human-readable warnings string is still produced.
    assert isinstance(converted.warnings, str)


# ---------------------------------------------------------------------------
# Typed stable importer entry: ImportRequest / import_work / ImportError
# ---------------------------------------------------------------------------


def test_import_work_returns_a_write_report(tmp_path: Path) -> None:
    # The stable entry takes a typed ImportRequest and RETURNS the writer's
    # WriteReport directly (the contract surface), with files actually written.
    from lib.writer import WriteReport

    content_root = tmp_path / "src" / "content"
    report = import_docx.import_work(import_docx.ImportRequest(
        docx=ROOT / "legacy/books/ru/23-личность-и-эго.docx",
        lang="ru",
        out_content=content_root,
        kind="book",
        number=90,
        slug="probe-work",
        title="Probe Work",
        description="Probe description.",
    ))

    assert isinstance(report, WriteReport)
    assert (content_root / "books" / "90-probe-work" / "ru.md").is_file()
    assert not report.refused


def test_import_work_refusal_returns_a_report_with_fatal_diagnostic(tmp_path: Path) -> None:
    # Re-importing an existing converter-owned <lang>.md without --replace is a
    # refusal. import_work must RETURN that refused report (with a fatal
    # diagnostic), NOT raise / SystemExit.
    content_root = tmp_path / "src" / "content"
    first = import_docx.import_work(import_docx.ImportRequest(
        docx=ROOT / "legacy/books/ru/23-личность-и-эго.docx",
        lang="ru",
        out_content=content_root,
        kind="book",
        number=90,
        slug="probe-work",
        title="Probe Work",
        description="Probe description.",
    ))
    assert not first.refused

    # Second import of the SAME bundle/lang, no replace -> the importer routes
    # through --into resolution by key; replace is False, so it is refused.
    second = import_docx.import_work(import_docx.ImportRequest(
        docx=ROOT / "legacy/books/ru/23-личность-и-эго.docx",
        lang="ru",
        out_content=content_root,
        into="90-probe-work",
        replace=False,
    ))
    assert second.refused, "an existing canonical_source without --replace must be refused"
    assert any(d.severity == "fatal" for d in second.diagnostics)


def test_import_work_missing_docx_raises_import_error(tmp_path: Path) -> None:
    content_root = tmp_path / "src" / "content"
    with pytest.raises(import_docx.ImportError):
        import_docx.import_work(import_docx.ImportRequest(
            docx=tmp_path / "does-not-exist.docx",
            lang="ru",
            out_content=content_root,
            kind="book",
        ))


def test_import_work_is_side_effect_free(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # The stable entry emits NO stdout/stderr (the CLI owns side effects).
    content_root = tmp_path / "src" / "content"
    capsys.readouterr()  # drain anything prior
    import_docx.import_work(import_docx.ImportRequest(
        docx=ROOT / "legacy/books/ru/23-личность-и-эго.docx",
        lang="ru",
        out_content=content_root,
        kind="book",
        number=90,
        slug="silent-probe",
        title="Silent Probe",
        description="d.",
    ))
    captured = capsys.readouterr()
    assert captured.out == "", f"import_work must not print to stdout; got {captured.out!r}"
    assert captured.err == "", f"import_work must not print to stderr; got {captured.err!r}"
