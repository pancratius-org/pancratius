from __future__ import annotations

import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from contextlib import suppress
from pathlib import Path

from pancratius import render_downloads
from pancratius.docx_merge import DocxMergeError, validate_docx_package
from pancratius.locales import Locale
from pancratius.ooxml import serialize_xml
from pancratius.pandoc import PandocNotFoundError, pandoc_argv0
from pancratius.translation.docx.align import (
    align_source_units,
    ignored_slot_diagnostics,
    join_markdown_units_for_word_slot,
    pair_markdown_units,
)
from pancratius.translation.docx.donor_docx import copy_docx_parts, word_text_slots
from pancratius.translation.docx.markdown_units import (
    PANDOC_TIMEOUT_SECONDS,
    markdown_cover_image,
    parse_markdown_transfer,
)
from pancratius.translation.docx.models import (
    BookDocxTranslationTarget,
    DocxTranslationError,
    MarkdownTransferUnit,
)
from pancratius.translation.docx.ooxml_write import (
    HyperlinkRelationshipAllocator,
    dedupe_media_payloads,
    footnote_reference_ids_by_body_order,
    remove_ignored_word_slots,
    repair_unbound_relationship_prefixes,
    replace_embedded_cover_data_uri,
    replace_footnotes,
    replace_paragraph_text,
    sanitize_drawing_metadata,
    unit_has_hyperlink,
    write_docx_parts,
)
from pancratius.writeplan import Diagnostic


def render_translated_docx(
    *,
    source_docx: Path,
    source_md: Path,
    translated_md: Path,
    out: Path,
) -> tuple[int, int, int, tuple[Diagnostic, ...]]:
    source = parse_markdown_transfer(source_md)
    translated = parse_markdown_transfer(translated_md)
    cover = markdown_cover_image(translated_md)
    pairing, pair_diags = pair_markdown_units(source, translated)
    diagnostics = list(pair_diags)
    if any(d.severity == "fatal" for d in diagnostics):
        return len(source.units), len(translated.units), 0, tuple(diagnostics)

    try:
        package = copy_docx_parts(source_docx)
        parts = dict(package.parts)
        document_root = ET.fromstring(parts["word/document.xml"])
    except DocxTranslationError as exc:
        diagnostics.append(Diagnostic("fatal", "docx-translate.invalid-docx", str(exc)))
        return len(source.units), len(translated.units), 0, tuple(diagnostics)
    except KeyError:
        diagnostics.append(Diagnostic(
            "fatal",
            "docx-translate.invalid-docx",
            f"{source_docx} has no word/document.xml",
        ))
        return len(source.units), len(translated.units), 0, tuple(diagnostics)
    except ET.ParseError as exc:
        diagnostics.append(Diagnostic(
            "fatal",
            "docx-translate.invalid-docx",
            f"{source_docx}:word/document.xml is not well-formed XML: {exc}",
        ))
        return len(source.units), len(translated.units), 0, tuple(diagnostics)

    slots = word_text_slots(document_root)
    try:
        alignment_plan = align_source_units(source, slots)
        diagnostics.extend(ignored_slot_diagnostics(alignment_plan.ignored_slots))
        document_hyperlinks = (
            HyperlinkRelationshipAllocator(parts, "word/_rels/document.xml.rels")
            if any(unit_has_hyperlink(unit) for unit in translated.units)
            else None
        )
        for alignment in alignment_plan.alignments:
            translated_members: list[MarkdownTransferUnit] = []
            for source_index in alignment.unit_indices:
                translated_indices = pairing.translated_indices_by_source[source_index]
                if translated_indices is None:
                    translated_members.append(MarkdownTransferUnit("blank"))
                else:
                    translated_members.extend(translated.units[index] for index in translated_indices)
            translated_unit = join_markdown_units_for_word_slot(
                tuple(translated_members)
            )
            replace_paragraph_text(
                alignment.slot.paragraph,
                translated_unit,
                hyperlinks=document_hyperlinks,
            )
        remove_ignored_word_slots(document_root, alignment_plan.ignored_slots)
        sanitize_drawing_metadata(document_root)
        diagnostics.extend(replace_embedded_cover_data_uri(document_root, cover))
        if any(d.severity == "fatal" for d in diagnostics):
            return len(source.units), len(translated.units), 0, tuple(diagnostics)
        if document_hyperlinks is not None:
            document_hyperlinks.save()
        parts["word/document.xml"] = serialize_xml(
            document_root,
            source_xml=package.parts["word/document.xml"],
        )
        replace_footnotes(
            parts,
            translated,
            reference_ids=footnote_reference_ids_by_body_order(document_root),
        )
    except DocxTranslationError as exc:
        diagnostics.append(Diagnostic("fatal", "docx-translate.transfer-failed", str(exc)))
        return len(source.units), len(translated.units), 0, tuple(diagnostics)
    except ET.ParseError as exc:
        diagnostics.append(Diagnostic(
            "fatal",
            "docx-translate.invalid-docx",
            f"{source_docx} contains malformed XML: {exc}",
        ))
        return len(source.units), len(translated.units), 0, tuple(diagnostics)

    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        write_docx_parts(parts, out, member_order=package.member_order)
        validation = validate_docx_package(out)
        del validation
    except (DocxTranslationError, DocxMergeError) as exc:
        if out.exists():
            with suppress(OSError):
                out.unlink()
        diagnostics.append(Diagnostic("fatal", "docx-translate.invalid-docx", str(exc)))
        return len(source.units), len(translated.units), 0, tuple(diagnostics)
    aligned_units = sum(len(alignment.unit_indices) for alignment in alignment_plan.alignments)
    return len(source.units), len(translated.units), aligned_units, tuple(diagnostics)


def render_markdown_docx(
    *,
    target: BookDocxTranslationTarget,
    lang: Locale,
    out: Path,
) -> tuple[int, int, int, tuple[Diagnostic, ...]]:
    """Render translated Markdown to DOCX when donor transplantation cannot apply.

    This backend preserves translated content through the public-download Markdown
    normalizer and uses the source DOCX only as a Pandoc reference document. It is
    intentionally explicit because it does not preserve donor paragraph/run
    structure the way ``render_translated_docx`` does.
    """
    diagnostics: list[Diagnostic] = [
        Diagnostic(
            "warning",
            "docx-translate.markdown-render-structure",
            (
                "rendered from translated Markdown using ru.docx as reference styles; "
                "donor paragraph/run structure was not transplanted"
            ),
        )
    ]
    try:
        with tempfile.TemporaryDirectory(prefix="pancratius-docx-markdown-render-") as tmp:
            scratch_dir = Path(tmp)
            work_entry = render_downloads.WorkEntry(
                kind="book",
                number=target.translated_entry.number,
                folder=target.source_entry.work_dir,
                lang=lang,
                md=target.translated_md,
                slug=target.translated_entry.slug,
                title=target.translated_entry.title,
            )
            export_root, _cover, image_map = render_downloads._stage_export_bundle(work_entry, scratch_dir)
            scratch_md = export_root / f"book-{target.number:02d}-{lang}.md"
            render_downloads._write_export_markdown(work_entry, scratch_md, image_map)
            raw_docx = scratch_dir / "pandoc.docx"
            subprocess.run(
                [
                    pandoc_argv0(),
                    str(scratch_md),
                    *_pandoc_from_for_markdown_docx(),
                    "--to",
                    "docx",
                    "--reference-doc",
                    str(target.source_docx),
                    "-o",
                    str(raw_docx),
                    "--resource-path",
                    str(export_root),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=PANDOC_TIMEOUT_SECONDS,
            )
            block_count = _markdown_render_block_count(scratch_md)
            _normalize_pandoc_docx(raw_docx, out)
            return block_count, block_count, block_count, tuple(diagnostics)
    except (FileNotFoundError, PandocNotFoundError):
        diagnostics.append(Diagnostic("fatal", "docx-translate.pandoc-missing", "pandoc not found"))
    except subprocess.TimeoutExpired:
        diagnostics.append(Diagnostic(
            "fatal",
            "docx-translate.pandoc-timeout",
            f"pandoc timed out after {PANDOC_TIMEOUT_SECONDS}s while rendering {target.translated_md}",
        ))
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        diagnostics.append(Diagnostic(
            "fatal",
            "docx-translate.pandoc-failed",
            f"pandoc failed while rendering {target.translated_md}: {detail}",
        ))
    except (DocxTranslationError, DocxMergeError, render_downloads.DownloadRenderError) as exc:
        diagnostics.append(Diagnostic("fatal", "docx-translate.markdown-render-failed", str(exc)))
    if out.exists():
        with suppress(OSError):
            out.unlink()
    return 0, 0, 0, tuple(diagnostics)


def _markdown_render_block_count(markdown: Path) -> int:
    body = markdown.read_text(encoding="utf-8")
    return sum(1 for block in re.split(r"\n{2,}", body) if block.strip())


def _pandoc_from_for_markdown_docx() -> list[str]:
    # Pandoc's Markdown reader treats a standalone image as a figure by default
    # and writes the alt text as a visible DOCX caption. Corpus Markdown uses
    # image alt for accessibility, not as body text, so the fallback source-DOCX
    # renderer disables that extension.
    return ["--from", "markdown-yaml_metadata_block-implicit_figures"]


def _normalize_pandoc_docx(raw_docx: Path, out: Path) -> None:
    package = copy_docx_parts(raw_docx)
    parts = {
        name: repair_unbound_relationship_prefixes(payload) if name.endswith(".xml") else payload
        for name, payload in package.parts.items()
    }
    dedupe_media_payloads(parts)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_docx_parts(parts, out, member_order=package.member_order)
    try:
        validation = validate_docx_package(out)
        del validation
    except DocxMergeError:
        with suppress(OSError):
            out.unlink()
        raise
