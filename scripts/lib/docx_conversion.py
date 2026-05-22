from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import sys
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import docx_to_md as legacy


@dataclass
class ConvertedDocx:
    body: str
    bibliography: list[dict[str, Any]] = field(default_factory=list)
    cross_refs: list[dict[str, Any]] = field(default_factory=list)
    warnings: str = ""


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
) -> ConvertedDocx:
    """Convert one DOCX into author-facing Markdown body + sidecar data.

    This is a small facade over the legacy batch converter's parsing pipeline.
    It deliberately does not read legacy catalogs or write manifests.
    """
    writes = legacy.WorkWrites(kind=kind, slug=work_key, work_dir=work_dir)
    image_records: list[legacy.ImageRecord] = []

    if kind == "poem":
        body, refs, _next_idx, warnings = legacy.convert_poem_docx_to_md(
            docx=docx,
            title=title,
            book_slug=f"poem-{work_key}",
            work_dir=work_dir,
            image_records=image_records,
            writes=writes,
            image_counter_start=1,
            cross_ref_title_index=title_index,
            own_ascii_slug=work_key,
        )
        return ConvertedDocx(
            body=body,
            cross_refs=legacy._restructure_cross_refs(refs),
            warnings=warnings,
        )

    image_book_slug = f"project-{work_key}" if kind == "project" else work_key
    body, biblio, refs, _next_idx, warnings, ast, structural_key_sequences = legacy.convert_docx_to_md(
        docx=docx,
        book_slug=image_book_slug,
        lang=lang,
        work_dir=work_dir,
        image_records=image_records,
        writes=writes,
        image_counter_start=1,
        biblio_slug_lookup=title_index,
        cross_ref_title_index=title_index,
        own_ascii_slug=work_key,
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
