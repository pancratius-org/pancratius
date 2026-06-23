from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from pancratius import import_docx
from pancratius.content_catalog import CatalogEntry, split_frontmatter
from pancratius.docx_conversion import ConvertedDocx
from pancratius.poem_chrome import PoemChrome, PoemSourceDate


def _poem_fm(
    *, source_date: str | None, existing: dict | None = None, ref: dict | None = None
) -> tuple[dict, list]:
    """Build poem frontmatter via `_frontmatter_for_import`, returning (fm, diagnostics)."""
    def entry(fm: dict) -> CatalogEntry:
        return CatalogEntry(
            kind="poem", number=1, slug="01-x", title="X", lang="ru", description="d",
            work_key="poem:1", work_dir=Path("."), md_path=Path("x.md"), frontmatter=fm,
        )
    converted = ConvertedDocx(
        body="стих\n",
        poem_chrome=(
            PoemChrome((PoemSourceDate(source_date),))
            if source_date is not None
            else PoemChrome()
        ),
    )
    fm = import_docx._frontmatter_for_import(
        request=import_docx.ImportRequest.for_new_work(
            docx=Path("x.docx"), lang="ru", out_content=Path("."), kind="poem"
        ),
        kind="poem", number=1, slug="01-x", title="X", description="d", lang="ru", cover=None,
        existing_lang=entry(existing) if existing is not None else None,
        reference=entry(ref) if ref is not None else None,
        converted=converted,
    )
    return fm, converted.diagnostics


def test_poem_signoff_date_fills_missing() -> None:
    fm, diags = _poem_fm(source_date="2025-03-14")
    assert fm["date"] == "2025-03-14"
    assert diags == []


def test_poem_signoff_date_preferred_over_reference() -> None:
    fm, diags = _poem_fm(source_date="2025-03-14", ref={"date": "2026-04-26"})
    assert fm["date"] == "2025-03-14"
    assert diags == []


def test_poem_signoff_date_never_overwrites_and_warns_on_mismatch() -> None:
    fm, diags = _poem_fm(source_date="2026-08-10", existing={"date": "2026-03-10"})
    assert fm["date"] == "2026-03-10"
    assert [d.code for d in diags] == ["import.poem-date-mismatch"]


requires_docx_import = pytest.mark.skipif(
    shutil.which("pandoc") is None or importlib.util.find_spec("PIL") is None,
    reason="pandoc and pillow are required",
)
docx_import_test = pytest.mark.pandoc
type DocxFactory = Callable[[str, str], Path]


def _frontmatter(path: Path) -> dict:
    fm, _body = split_frontmatter(path.read_text(encoding="utf-8"))
    return fm


@pytest.fixture
def make_docx(tmp_path: Path) -> DocxFactory:
    """Factory for tiny DOCX fixtures used by importer contract tests.

    These tests exercise the importer/writer boundary, not a specific corpus
    source file. Generating the DOCX keeps them independent of removed source
    archives and of mutable release artifacts under src/content/.
    """

    def make(name: str, markdown: str = "# Fixture\n\nBody.") -> Path:
        md = tmp_path / f"{Path(name).stem}.md"
        docx = tmp_path / name
        md.write_text(markdown, encoding="utf-8")
        subprocess.run(
            ["pandoc", str(md), "-o", str(docx)],
            check=True,
            capture_output=True,
            text=True,
        )
        return docx

    return make


@docx_import_test
@requires_docx_import
def test_import_new_docx_creates_bundle_paths_and_frontmatter(
    tmp_path: Path,
    make_docx: DocxFactory,
) -> None:
    docx = make_docx("source-ru.docx", "# Личность и эго\n\nТекст.")
    content_root = tmp_path / "src" / "content"
    video_dir = content_root / "videos" / "draft-video"
    video_dir.mkdir(parents=True)
    (video_dir / "ru.md").write_text(
        "---\nkind: video\ntitle: broken: draft\n---\n\nDraft.\n",
        encoding="utf-8",
    )

    report = import_docx.import_work(import_docx.ImportRequest.for_new_work(
        docx=docx,
        lang="ru",
        out_content=content_root,
        kind="book",
        number=90,
        slug="probe-work",
        title="Probe Work",
        description="Probe description.",
    ))

    work_dir = content_root / "books" / "90-probe-work"
    assert not report.refused
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


@docx_import_test
@requires_docx_import
def test_import_translation_with_into_updates_existing_bundle(
    tmp_path: Path,
    make_docx: DocxFactory,
) -> None:
    docx = make_docx("source-en.docx", "# Message to Muslims\n\nEnglish body.")
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

    report = import_docx.import_work(import_docx.ImportRequest.for_existing_work(
        docx=docx,
        lang="en",
        out_content=content_root,
        into="30-poslanie-musulmanam",
    ))
    assert not report.refused

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


# ---------------------------------------------------------------------------
# Fix A: typed converter diagnostics flow into the WritePlan and a FATAL blocks
# ---------------------------------------------------------------------------


@docx_import_test
@requires_docx_import
def test_converter_fatal_diagnostic_blocks_the_write(
    tmp_path: Path,
    make_docx: DocxFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docx = make_docx("source-ru.docx", "# Fatal Probe\n\nТекст.")
    # A converter-side FATAL (the unresolvable-local-image fatal proven in
    # test_ir_pipeline) must reach WritePlan.diagnostics and make the writer REFUSE —
    # nothing written. Today the converter flattened diagnostics into a warning STRING,
    # so a fatal could not block. We inject the fatal at the asset pass (its real
    # trigger) and assert the whole bundle is refused.
    from pancratius import ir
    from pancratius.passes import assets
    from pancratius.writeplan import PlannedAsset

    real_plan = assets.plan_assets

    def fatal_plan(
        doc: ir.Document, media_root: Path, diagnostics: list[ir.Diagnostic]
    ) -> tuple[ir.Document, list[PlannedAsset]]:
        out_doc, planned = real_plan(doc, media_root, diagnostics)
        diagnostics.append(
            ir.Diagnostic("fatal", "import.image-unresolved", "synthetic fatal")
        )
        return out_doc, planned

    monkeypatch.setattr(assets, "plan_assets", fatal_plan)

    content_root = tmp_path / "src" / "content"
    report = import_docx.import_work(import_docx.ImportRequest.for_new_work(
        docx=docx,
        lang="ru",
        out_content=content_root,
        kind="book",
        number=91,
        slug="fatal-probe",
        title="Fatal Probe",
        description="d.",
    ))

    work_dir = content_root / "books" / "91-fatal-probe"
    assert report.refused
    assert not (work_dir / "ru.md").exists(), "a converter FATAL must block the write entirely"


@docx_import_test
@requires_docx_import
def test_converter_typed_diagnostics_carry_severity(
    tmp_path: Path,
    make_docx: DocxFactory,
) -> None:
    docx = make_docx("source-ru.docx", "# Probe\n\nТекст.")
    # The converter must expose TYPED diagnostics (not just a flattened string), so a
    # downstream consumer can read severity. A clean import still carries any
    # info/warning diagnostics with their severity intact.
    from pancratius import content_catalog
    from pancratius.docx_conversion import convert_single_docx

    media = tmp_path / "media"
    converted = convert_single_docx(
        docx,
        kind="book", lang="ru", work_key="91-probe", title="Probe",
        title_index=content_catalog.build_title_index([]),
        media_out=media,
    )
    assert hasattr(converted, "diagnostics"), "ConvertedDocx must expose typed diagnostics"
    assert all(d.severity in {"fatal", "warning", "info"} for d in converted.diagnostics)
    # The human-readable warnings string is still produced.
    assert isinstance(converted.warnings, str)


# ---------------------------------------------------------------------------
# Typed stable importer entry: ImportRequest / import_work / ImportWorkError
# ---------------------------------------------------------------------------


@docx_import_test
@requires_docx_import
def test_import_work_returns_a_write_report(tmp_path: Path, make_docx: DocxFactory) -> None:
    docx = make_docx("source-ru.docx", "# Probe Work\n\nТекст.")
    # The stable entry takes a typed ImportRequest and RETURNS the writer's
    # WriteReport directly (the contract surface), with files actually written.
    from pancratius.writer import WriteReport

    content_root = tmp_path / "src" / "content"
    report = import_docx.import_work(import_docx.ImportRequest.for_new_work(
        docx=docx,
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


@docx_import_test
@requires_docx_import
def test_import_work_explicit_target_creates_when_absent(tmp_path: Path, make_docx: DocxFactory) -> None:
    docx = make_docx("source-ru.docx", "# Explicit Work\n\nТекст.")
    content_root = tmp_path / "src" / "content"
    report = import_docx.import_work(import_docx.ImportRequest.for_explicit_work(
        docx=docx,
        lang="ru",
        out_content=content_root,
        kind="book",
        number=91,
        slug="explicit-work",
        title="Explicit Work",
        description="Explicit description.",
    ))

    assert (content_root / "books" / "91-explicit-work" / "ru.md").is_file()
    assert not report.refused


@docx_import_test
@requires_docx_import
def test_import_work_explicit_existing_same_lang_without_replace_is_refused(
    tmp_path: Path,
    make_docx: DocxFactory,
) -> None:
    docx = make_docx("source-ru.docx", "# Explicit Work\n\nТекст.")
    content_root = tmp_path / "src" / "content"
    first = import_docx.import_work(import_docx.ImportRequest.for_explicit_work(
        docx=docx,
        lang="ru",
        out_content=content_root,
        kind="book",
        number=91,
        slug="explicit-work",
        title="Explicit Work",
        description="Explicit description.",
    ))
    assert not first.refused

    second = import_docx.import_work(import_docx.ImportRequest.for_explicit_work(
        docx=docx,
        lang="ru",
        out_content=content_root,
        kind="book",
        number=91,
        title="Explicit Work",
        replace=False,
    ))

    assert second.refused
    assert any(d.severity == "fatal" for d in second.diagnostics)


@docx_import_test
@requires_docx_import
def test_import_work_explicit_existing_new_lang_does_not_require_replace(
    tmp_path: Path,
    make_docx: DocxFactory,
) -> None:
    docx = make_docx("source.docx", "# Explicit Work\n\nТекст.")
    content_root = tmp_path / "src" / "content"
    first = import_docx.import_work(import_docx.ImportRequest.for_explicit_work(
        docx=docx,
        lang="ru",
        out_content=content_root,
        kind="book",
        number=91,
        slug="explicit-work",
        title="Explicit Work",
        description="Explicit description.",
    ))
    assert not first.refused

    second = import_docx.import_work(import_docx.ImportRequest.for_explicit_work(
        docx=docx,
        lang="en",
        out_content=content_root,
        kind="book",
        number=91,
        slug="explicit-work-en",
        title="Explicit Work EN",
        description="Explicit description EN.",
        replace=False,
    ))

    assert not second.refused
    en_md = content_root / "books" / "91-explicit-work" / "en.md"
    assert en_md.is_file()
    fm, _body = split_frontmatter(en_md.read_text(encoding="utf-8"))
    assert fm["slug"] == "91-explicit-work-en"


@docx_import_test
@requires_docx_import
def test_import_work_refusal_returns_a_report_with_fatal_diagnostic(
    tmp_path: Path,
    make_docx: DocxFactory,
) -> None:
    docx = make_docx("source-ru.docx", "# Probe Work\n\nТекст.")
    # Re-importing an existing converter-owned <lang>.md without --replace is a
    # refusal. import_work must RETURN that refused report (with a fatal
    # diagnostic), NOT raise / SystemExit.
    content_root = tmp_path / "src" / "content"
    first = import_docx.import_work(import_docx.ImportRequest.for_new_work(
        docx=docx,
        lang="ru",
        out_content=content_root,
        kind="book",
        number=90,
        slug="probe-work",
        title="Probe Work",
        description="Probe description.",
    ))
    assert not first.refused

    # Second import of the SAME bundle/lang, no replace -> the importer resolves
    # the existing key; replace is False, so it is refused.
    second = import_docx.import_work(import_docx.ImportRequest.for_existing_work(
        docx=docx,
        lang="ru",
        out_content=content_root,
        into="90-probe-work",
        replace=False,
    ))
    assert second.refused, "an existing canonical_source without --replace must be refused"
    assert any(d.severity == "fatal" for d in second.diagnostics)


def test_import_work_missing_docx_raises_import_work_error(tmp_path: Path) -> None:
    content_root = tmp_path / "src" / "content"
    with pytest.raises(import_docx.ImportWorkError, match="DOCX not found"):
        import_docx.import_work(import_docx.ImportRequest.for_new_work(
            docx=tmp_path / "does-not-exist.docx",
            lang="ru",
            out_content=content_root,
            kind="book",
        ))


# ---------------------------------------------------------------------------
# Provenance relayer: the manifest is written by the IMPORT ENTRY (not the
# writer), sandboxed under the content root's `data/imports/`.
# ---------------------------------------------------------------------------


@docx_import_test
@requires_docx_import
def test_real_import_writes_manifest_under_content_root(
    tmp_path: Path,
    make_docx: DocxFactory,
) -> None:
    docx = make_docx("source-ru.docx", "# Probe Work\n\nТекст.")
    # A real (non-dry-run) import must write the per-import provenance manifest at
    # `<tmp>/data/imports/<scope>.json` (derived from the temp content root, so the
    # real repo data/imports is NEVER touched), with the source sha256 set and the
    # source_document pointing at the input DOCX.
    content_root = tmp_path / "src" / "content"
    report = import_docx.import_work(import_docx.ImportRequest.for_new_work(
        docx=docx,
        lang="ru",
        out_content=content_root,
        kind="book",
        number=90,
        slug="probe-work",
        title="Probe Work",
        description="Probe description.",
    ))
    assert not report.refused

    manifest_path = tmp_path / "data" / "imports" / "books-90-probe-work.json"
    assert manifest_path.is_file(), "the import entry must write the manifest under the content root's data/imports"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["target_scope"] == "books/90-probe-work"
    assert manifest["source_document"] == str(docx.resolve())
    assert manifest["source_sha256"]


@docx_import_test
@requires_docx_import
def test_dry_run_import_writes_no_manifest(
    tmp_path: Path,
    make_docx: DocxFactory,
) -> None:
    docx = make_docx("source-ru.docx", "# Probe Work\n\nТекст.")
    # A dry-run import touches NOTHING — no bundle, and no provenance manifest.
    content_root = tmp_path / "src" / "content"
    report = import_docx.import_work(import_docx.ImportRequest.for_new_work(
        docx=docx,
        lang="ru",
        out_content=content_root,
        kind="book",
        number=90,
        slug="probe-work",
        title="Probe Work",
        description="Probe description.",
        dry_run=True,
    ))
    assert not report.refused
    assert not (tmp_path / "data" / "imports").exists(), "dry-run must write no manifest"
    assert not (content_root / "books").exists(), "dry-run must write no bundle"


@docx_import_test
@requires_docx_import
def test_import_work_is_side_effect_free(
    tmp_path: Path,
    make_docx: DocxFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    docx = make_docx("source-ru.docx", "# Silent Probe\n\nТекст.")
    # The stable entry emits NO stdout/stderr (the CLI owns side effects).
    content_root = tmp_path / "src" / "content"
    capsys.readouterr()  # drain anything prior
    import_docx.import_work(import_docx.ImportRequest.for_new_work(
        docx=docx,
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


def test_imports_dir_canonical_layout() -> None:
    # Manifest lands at <root>/data/imports for the canonical <root>/src/content.
    assert import_docx._imports_dir(Path("/x/src/content")) == Path("/x/data/imports")


def test_imports_dir_rejects_shallow_out_content() -> None:
    # Too shallow to have a grandparent → a clean input error, not an IndexError.
    with pytest.raises(import_docx.ImportWorkError, match="must be shaped like"):
        import_docx._imports_dir(Path("/content"))
