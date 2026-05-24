from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import sys
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from lib import docx_adapter, docx_engine as legacy, ir_lower, ir_normalize

# PHASE-7 RE-HOME NOTE (do not act now): the LIVE IR path still reaches into the
# `docx_engine` (`legacy`) module for a handful of helpers that pre-date the IR
# pipeline and were never re-homed. Before `docx_engine` can be deleted in Phase 7,
# these must move into the IR modules (or a shared util):
#   * here (`docx_conversion`): `_strip_source_duplicate_poem_title`,
#     `read_docx_paragraph_meta`, `_dedupe_bibliography`, `_restructure_cross_refs`,
#     `extract_cross_refs`, plus `to_ascii_slug` / `WorkWrites` / `ImageRecord` /
#     `PlannedAsset` / `_write_bibliography_sidecar`;
#   * `ir_lower`: the asset helpers `_body_image_alt`, `_escape_markdown_alt`,
#     `_hash_file`, `_is_image_path`, `_normalize_ext`, `PlannedAsset`, `RASTER_CAP_EXTS`;
#   * `ir_normalize`: `AI_ALT_FRAGMENTS`, `RIGHTS_PATTERNS`.
# (The GFM-oracle path `convert_single_docx_gfm` is itself deleted in Phase 7, so
# its `legacy` use is expected to disappear with the engine.)


@dataclass
class ConvertedDocx:
    body: str
    bibliography: list[dict[str, Any]] = field(default_factory=list)
    cross_refs: list[dict[str, Any]] = field(default_factory=list)
    warnings: str = ""
    # Planned body images the conversion REFERENCES but does not copy. The
    # converter is now pure w.r.t. the filesystem (it only reads the extracted
    # media); the importer turns these into writer `transform_asset` ops, so the
    # writer is the sole component that copies them into the bundle.
    assets: list[legacy.PlannedAsset] = field(default_factory=list)


def to_ascii_slug(value: str) -> str:
    return legacy.to_ascii_slug(value)


def convert_single_docx(
    docx: Path,
    *,
    kind: str,
    lang: str,
    work_key: str,
    title: str,
    work_dir: Path,
    title_index: dict[str, tuple[str, int | None, str | None]],
    media_out: Path,
) -> ConvertedDocx:
    """Convert one DOCX into author-facing Markdown body + sidecar data + the
    PLANNED body assets — through the typed-IR pipeline (adapter → normalize →
    lower).

    This is the LIVE conversion path (6.2 cutover): ``import_docx`` calls this and
    gets IR output. It deliberately does not read legacy catalogs or write
    manifests, and it does not copy media: pandoc extracts media into the
    caller-provided PERSISTENT ``media_out``, and the returned
    ``ConvertedDocx.assets`` reference those files. ``media_out`` must outlive this
    call until the writer copies the assets.

    The pipeline is pure after the adapter: ``ir_normalize``/``ir_lower`` perform
    no filesystem mutation; the adapter shells out to pandoc and extracts media
    only. The GFM engine (``convert_single_docx_gfm``) is retained as the A/B
    oracle (``scripts/audit/ir_ab_corpus.py``); it is deleted in Phase 7 after
    final sign-off.
    """
    media_out.mkdir(parents=True, exist_ok=True)
    doc = docx_adapter.adapt(docx, media_out)

    if kind == "poem":
        # Poems are verse end-to-end: the GFM poem path does not demote headings,
        # lift a bibliography, or run structural/verse detection (it renders the
        # whole AST as verse via `pandoc_poem_ast_to_md`). Mirror that by skipping
        # heading demotion and bibliography lift; light cleanup still applies.
        doc.blocks = ir_normalize.drop_toc(doc.blocks)
        doc.blocks = ir_normalize.scrub_ai_alt(doc.blocks)
        doc.blocks = ir_normalize.thematic_breaks(doc.blocks)
        doc.blocks = ir_normalize.strip_formatting_artifacts(doc.blocks)
    else:
        ir_normalize.normalize(doc, demote_levels=1, slug_lookup=title_index)

    assets = ir_lower.assign_assets(doc, media_out, lang)
    body = ir_lower.lower(doc, lang, poem=(kind == "poem"))
    if kind == "poem":
        # Mirror the GFM poem path's source-duplicate-title strip: when the DOCX
        # itself starts with a title paragraph (the masthead already renders that
        # title) the first stanza repeats the page title and must be dropped — the
        # same drop the DOCX stanza oracle (`poetry_stanzas.expected_groups`)
        # applies. Reuse the GFM helpers so the strip decision is byte-identical to
        # the live path (bold / line-break source signals, not a string guess).
        body = legacy._strip_source_duplicate_poem_title(
            body, title, legacy.read_docx_paragraph_meta(docx)
        )
    cross_refs = legacy.extract_cross_refs(body, work_key, title_index)
    # Propagate pandoc warnings AND any SURFACED adapter/normalize diagnostic
    # (severity `warning`/`fatal`) to the caller — not just `import.pandoc-warn`.
    # The C1 fix added `import.align-unreconciled` (right-aligned source paragraphs
    # that no longer reconcile onto the AST); forwarding warnings here is what makes
    # the documented "fail loud" actually fire instead of being discarded.
    warning_messages = [
        d.message for d in doc.diagnostics if d.code == "import.pandoc-warn"
    ]
    warning_messages.extend(
        f"[{d.code}] {d.message}"
        for d in doc.diagnostics
        if d.severity in {"warning", "fatal"}
    )
    warnings = "\n".join(warning_messages)
    return ConvertedDocx(
        body=body,
        bibliography=legacy._dedupe_bibliography(doc.bibliography),
        cross_refs=legacy._restructure_cross_refs(cross_refs),
        warnings=warnings,
        assets=assets,
    )


def convert_single_docx_gfm(
    docx: Path,
    *,
    kind: str,
    lang: str,
    work_key: str,
    title: str,
    work_dir: Path,
    title_index: dict[str, tuple[str, int | None, str | None]],
    media_out: Path,
) -> ConvertedDocx:
    """Convert one DOCX through the legacy GFM engine (markdown-string patching),
    returning the same ``ConvertedDocx`` shape the live IR path returns.

    This is NO LONGER the live path — ``convert_single_docx`` (the typed-IR
    pipeline) is, after the 6.2 cutover. This is retained ONLY as the A/B ORACLE:
    ``scripts/audit/ir_ab_corpus.py`` runs both engines over the real corpus and
    asserts the IR-live body loses no reading content vs this GFM oracle. The
    signature matches ``convert_single_docx`` exactly. Deleted in Phase 7 after
    final sign-off, together with ``lib.docx_engine``.
    """
    writes = legacy.WorkWrites(kind=kind, slug=work_key, work_dir=work_dir)
    image_records: list[legacy.ImageRecord] = []

    if kind == "poem":
        body, refs, _next_idx, warnings, assets = legacy.convert_poem_docx_to_md(
            docx=docx,
            title=title,
            book_slug=f"poem-{work_key}",
            work_dir=work_dir,
            image_records=image_records,
            writes=writes,
            image_counter_start=1,
            cross_ref_title_index=title_index,
            own_ascii_slug=work_key,
            media_out=media_out,
        )
        return ConvertedDocx(
            body=body,
            cross_refs=legacy._restructure_cross_refs(refs),
            warnings=warnings,
            assets=assets,
        )

    # kind is only ever book/poem here (import_docx passes lib.kinds.WORK_KINDS);
    # the image book_slug is just the work key for those.
    (
        body,
        biblio,
        refs,
        _next_idx,
        warnings,
        ast,
        structural_key_sequences,
        assets,
    ) = legacy.convert_docx_to_md(
        docx=docx,
        book_slug=work_key,
        lang=lang,
        work_dir=work_dir,
        image_records=image_records,
        writes=writes,
        image_counter_start=1,
        biblio_slug_lookup=title_index,
        cross_ref_title_index=title_index,
        own_ascii_slug=work_key,
        media_out=media_out,
    )
    body = legacy.demote_markdown_headings(body, 1)
    body = legacy.normalize_ast_verse_sections(body, ast)
    body = legacy.normalize_ast_lineated_runs(body, ast, structural_key_sequences)
    body = legacy.normalize_dedication_verse_sections(body)
    body = legacy.collapse_blank_lines(body)
    return ConvertedDocx(
        body=body,
        bibliography=legacy._dedupe_bibliography(biblio),
        cross_refs=legacy._restructure_cross_refs(refs),
        warnings=warnings,
        assets=assets,
    )


def write_bibliography_sidecar(
    work_dir: Path,
    kind: str,
    lang: str,
    bibliography: list[dict[str, Any]],
) -> None:
    if not bibliography:
        return
    writes = legacy.WorkWrites(kind=kind, slug=work_dir.name, work_dir=work_dir)
    legacy._write_bibliography_sidecar(work_dir, {lang: bibliography}, writes)
