from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import sys
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from lib import docx_engine as legacy


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
    PLANNED body assets.

    This is a small facade over the legacy batch converter's parsing pipeline.
    It deliberately does not read legacy catalogs or write manifests, and it does
    not copy media: pandoc extracts media into the caller-provided PERSISTENT
    `media_out`, and the returned `ConvertedDocx.assets` reference those files.
    `media_out` must outlive this call until the writer copies the assets.
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
