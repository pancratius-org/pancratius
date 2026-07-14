"""OOXML outline repair used by ``pancratius docx merge --part``.

When old multipart works are collapsed into a single source DOCX, the old
pipeline-injected "Part N" Markdown headings must become real source headings.
This module inserts those part headings into ``word/document.xml`` and demotes
the existing part-internal Heading 1 paragraphs to Heading 2, so the normal
importer emits:

    source Heading 1 -> Markdown H2 (part)
    source Heading 2 -> Markdown H3 (chapter)

It is intentionally narrow: it does not merge relationships/media/styles across
separate DOCX files. The user-facing operation is ``pancratius docx merge``;
this module is the outline substep after a faithful physical merge.
"""

from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path

from pancratius.docx_source import DocxSourceError, analyze_paragraph
from pancratius.ooxml import W, parse_xml, serialize_xml


class DocxOutlineError(ValueError):
    """The requested outline operation cannot be applied safely."""


@dataclass(frozen=True)
class PartBoundary:
    title: str
    first_heading_prefix: str


@dataclass(frozen=True)
class OutlineSummary:
    source: Path
    output: Path
    inserted_parts: int
    removed_toc_blocks: int
    demoted_headings: int
    cleared_empty_headings: int


@dataclass(frozen=True, slots=True)
class HeadingDemotionCounts:
    demoted: int
    cleared_empty: int


def _w_val(el: ET.Element | None) -> str:
    return "" if el is None else el.get(f"{W}val", "")


def _paragraph_text(p: ET.Element) -> str:
    try:
        return analyze_paragraph(p).text
    except DocxSourceError as exc:
        raise DocxOutlineError(f"unsupported source paragraph: {exc}") from exc


def _pstyle(p: ET.Element) -> ET.Element | None:
    return p.find(f"./{W}pPr/{W}pStyle")


def _paragraph_style(p: ET.Element) -> str:
    return _w_val(_pstyle(p))


def _style_name(style: ET.Element) -> str:
    return _w_val(style.find(f"{W}name")).lower()


def _style_outline_level(style: ET.Element) -> str:
    return _w_val(style.find(f"{W}pPr/{W}outlineLvl"))


def _paragraph_styles(styles_root: ET.Element) -> list[ET.Element]:
    return [
        style for style in styles_root.findall(f"{W}style")
        if style.get(f"{W}type") in {None, "paragraph"}
    ]


def _heading1_style_ids(styles_root: ET.Element) -> set[str]:
    out: set[str] = set()
    for style in _paragraph_styles(styles_root):
        style_id = style.get(f"{W}styleId", "")
        name = _style_name(style)
        outline = _style_outline_level(style)
        if style_id in {"1", "Heading1"} or (name == "heading 1" and outline in {"", "0"}):
            out.add(style_id)
    if not out:
        raise DocxOutlineError("no Heading 1 paragraph style found in styles.xml")
    return out


def _preferred_heading1_style_id(heading1_ids: set[str]) -> str:
    for preferred in ("1", "Heading1"):
        if preferred in heading1_ids:
            return preferred
    return sorted(heading1_ids)[0]


def _ensure_child(parent: ET.Element, tag: str) -> ET.Element:
    child = parent.find(tag)
    if child is None:
        child = ET.SubElement(parent, tag)
    return child


def _ensure_heading2_style(styles_root: ET.Element, heading1_style_id: str) -> str:
    for preferred in ("21", "Heading2", "2"):
        heading2 = styles_root.find(f".//{W}style[@{W}styleId='{preferred}']")
        if heading2 is not None and _style_name(heading2) == "heading 2":
            ppr = _ensure_child(heading2, f"{W}pPr")
            outline = _ensure_child(ppr, f"{W}outlineLvl")
            outline.set(f"{W}val", "1")
            return preferred

    heading2 = next(
        (
            style for style in _paragraph_styles(styles_root)
            if _style_name(style) == "heading 2" and _style_outline_level(style) == "1"
        ),
        None,
    )
    if heading2 is not None:
        style_id = heading2.get(f"{W}styleId", "")
        if style_id:
            return style_id

    heading2 = styles_root.find(f".//{W}style[@{W}styleId='Heading2']")
    if heading2 is None:
        source = styles_root.find(f".//{W}style[@{W}styleId='{heading1_style_id}']")
        if source is None:
            raise DocxOutlineError(f"Heading 1 style {heading1_style_id!r} is missing from styles.xml")
        heading2 = copy.deepcopy(source)
        heading2.set(f"{W}styleId", "Heading2")
        styles_root.append(heading2)

    name = _ensure_child(heading2, f"{W}name")
    name.set(f"{W}val", "heading 2")
    ppr = _ensure_child(heading2, f"{W}pPr")
    outline = _ensure_child(ppr, f"{W}outlineLvl")
    outline.set(f"{W}val", "1")
    return "Heading2"


def _part_paragraph(title: str, heading1_style_id: str) -> ET.Element:
    p = ET.Element(f"{W}p")
    ppr = ET.SubElement(p, f"{W}pPr")
    style = ET.SubElement(ppr, f"{W}pStyle")
    style.set(f"{W}val", heading1_style_id)
    run = ET.SubElement(p, f"{W}r")
    text = ET.SubElement(run, f"{W}t")
    text.text = title
    return p


def _body_children(root: ET.Element) -> tuple[ET.Element, list[ET.Element]]:
    body = root.find(f"{W}body")
    if body is None:
        raise DocxOutlineError("word/document.xml has no w:body")
    return body, list(body)


def _is_toc_block(child: ET.Element) -> bool:
    if child.tag != f"{W}sdt":
        return False
    field_codes = " ".join(t.text or "" for t in child.findall(f".//{W}instrText"))
    return bool(field_codes.strip().startswith("TOC "))


def _drop_generated_toc_blocks(children: list[ET.Element]) -> tuple[list[ET.Element], int]:
    kept: list[ET.Element] = []
    removed = 0
    for child in children:
        if _is_toc_block(child):
            removed += 1
        else:
            kept.append(child)
    return kept, removed


def _part_insertions(children: list[ET.Element], parts: tuple[PartBoundary, ...]) -> dict[int, str]:
    existing_titles = {
        _paragraph_text(child) for child in children
        if child.tag == f"{W}p"
    }
    duplicates = sorted(part.title for part in parts if part.title in existing_titles)
    if duplicates:
        raise DocxOutlineError(
            "part headings already exist in source DOCX: " + ", ".join(repr(title) for title in duplicates)
        )

    insertions: dict[int, str] = {}
    for part in parts:
        matches = [
            index for index, child in enumerate(children)
            if child.tag == f"{W}p" and _paragraph_text(child).startswith(part.first_heading_prefix)
        ]
        if not matches:
            raise DocxOutlineError(
                f"no paragraph starts with marker {part.first_heading_prefix!r} for {part.title!r}"
            )
        if len(matches) > 1:
            raise DocxOutlineError(
                f"marker {part.first_heading_prefix!r} for {part.title!r} is ambiguous "
                f"({len(matches)} matches)"
            )
        target = matches[0]
        if existing := insertions.get(target):
            raise DocxOutlineError(
                f"part markers for {existing!r} and {part.title!r} target the same paragraph"
            )
        insertions[target] = part.title
    return insertions


def _demote_heading1_paragraphs(
    children: list[ET.Element],
    heading1_ids: set[str],
    heading2_id: str,
    *,
    first_part_index: int,
) -> HeadingDemotionCounts:
    demoted = 0
    cleared = 0
    for index, child in enumerate(children):
        if index < first_part_index:
            continue
        if child.tag != f"{W}p":
            continue
        style = _pstyle(child)
        if style is not None and _paragraph_style(child) in heading1_ids:
            if not _paragraph_text(child):
                parent = child.find(f"./{W}pPr")
                if parent is not None:
                    parent.remove(style)
                cleared += 1
                continue
            style.set(f"{W}val", heading2_id)
            demoted += 1
    return HeadingDemotionCounts(demoted=demoted, cleared_empty=cleared)


def apply_part_outline(
    source: Path,
    output: Path,
    parts: tuple[PartBoundary, ...],
) -> OutlineSummary:
    if not parts:
        raise DocxOutlineError("at least one --part is required")
    if source.suffix.lower() != ".docx":
        raise DocxOutlineError(f"expected a .docx source, got {source}")
    if not source.is_file():
        raise DocxOutlineError(f"DOCX not found: {source}")

    with zipfile.ZipFile(source) as zf:
        payload = {name: zf.read(name) for name in zf.namelist()}
    try:
        document_xml = payload["word/document.xml"]
        styles_xml = payload["word/styles.xml"]
    except KeyError as exc:
        raise DocxOutlineError(f"{source} is missing {exc.args[0]}") from exc

    document = parse_xml(document_xml)
    styles = parse_xml(styles_xml)
    document_root = document.root
    styles_root = styles.root
    body, children = _body_children(document_root)
    children, removed_toc = _drop_generated_toc_blocks(children)
    heading1_ids = _heading1_style_ids(styles_root)
    heading1_id = _preferred_heading1_style_id(heading1_ids)
    heading2_id = _ensure_heading2_style(styles_root, heading1_id)
    insertions = _part_insertions(children, parts)
    demotion_counts = _demote_heading1_paragraphs(
        children,
        heading1_ids,
        heading2_id,
        first_part_index=min(insertions),
    )

    for child in list(body):
        body.remove(child)
    for index, child in enumerate(children):
        if title := insertions.get(index):
            body.append(_part_paragraph(title, heading1_id))
        body.append(child)

    output.parent.mkdir(parents=True, exist_ok=True)
    payload["word/document.xml"] = serialize_xml(document)
    payload["word/styles.xml"] = serialize_xml(styles)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in payload.items():
            zf.writestr(name, data)

    return OutlineSummary(
        source=source,
        output=output,
        inserted_parts=len(parts),
        removed_toc_blocks=removed_toc,
        demoted_headings=demotion_counts.demoted,
        cleared_empty_headings=demotion_counts.cleared_empty,
    )


def parse_part_spec(raw: str) -> PartBoundary:
    title, sep, marker = raw.partition("::")
    if not sep or not title.strip() or not marker.strip():
        raise DocxOutlineError("part specs must be shaped like 'Part title::first heading prefix'")
    return PartBoundary(title=title.strip(), first_heading_prefix=marker.strip())
