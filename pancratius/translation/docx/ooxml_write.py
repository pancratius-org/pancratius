from __future__ import annotations

import copy
import hashlib
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import assert_never

from pancratius.ooxml import (
    DRAWING_METADATA_DESCRIPTION_ATTR,
    DRAWING_METADATA_ELEMENT_TAGS,
    DRAWING_METADATA_NAME_ATTR,
    DRAWING_METADATA_TITLE_ATTR,
    DRAWING_METADATA_WORD_PART_RE,
    HYPERLINK_REL_TYPE,
    REL,
    XML_SPACE,
    R,
    W,
    serialize_relationships,
    serialize_xml,
)
from pancratius.translation.docx.align import normalize_transfer_text
from pancratius.translation.docx.donor_docx import word_paragraph_text
from pancratius.translation.docx.models import (
    DocxTranslationError,
    FootnoteAnchor,
    IgnoredWordSlot,
    MarkdownCoverImage,
    MarkdownTransferDocument,
    MarkdownTransferUnit,
    TranslatedTextRun,
)
from pancratius.writeplan import Diagnostic

FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
UNBOUND_REL_ATTR_RE = re.compile(
    rb"(?<![A-Za-z0-9_.-])([A-Za-z_][A-Za-z0-9_.-]*):(id|embed|link)="
)
RELATIONSHIP_REF_ATTRS = frozenset({f"{R}id", f"{R}embed", f"{R}link"})

type OoxmlPartName = str
type RelationshipId = str


@dataclass(slots=True)
class HyperlinkRelationshipAllocator:
    """Creates external hyperlink relationships for one OOXML part."""

    parts: dict[str, bytes]
    rels_part: str
    root: ET.Element = field(init=False)
    source_xml: bytes | None = field(init=False)
    next_id: int = field(init=False)

    def __post_init__(self) -> None:
        if self.rels_part in self.parts:
            self.source_xml = self.parts[self.rels_part]
            self.root = ET.fromstring(self.source_xml)
        else:
            self.source_xml = None
            self.root = ET.Element(f"{REL}Relationships")
        ids: list[int] = []
        for rel in self.root.findall(f"{REL}Relationship"):
            rel_id = str(rel.get("Id") or "")
            match = re.fullmatch(r"rId(\d+)", rel_id)
            if match:
                ids.append(int(match.group(1)))
        self.next_id = max(ids, default=0) + 1

    def add_external_hyperlink(self, target: str) -> str:
        rel_id = f"rId{self.next_id}"
        self.next_id += 1
        rel = ET.SubElement(self.root, f"{REL}Relationship")
        rel.set("Id", rel_id)
        rel.set("Type", HYPERLINK_REL_TYPE)
        rel.set("Target", target)
        rel.set("TargetMode", "External")
        return rel_id

    def save(self) -> None:
        self.parts[self.rels_part] = serialize_relationships(self.root, source_xml=self.source_xml)


def _clone_run_properties(
    base: ET.Element | None,
    *,
    strong: bool,
    emphasis: bool,
    hyperlink: bool = False,
) -> ET.Element | None:
    rpr = copy.deepcopy(base) if base is not None else ET.Element(f"{W}rPr")
    for style in rpr.findall(f"{W}rStyle"):
        rpr.remove(style)
    for vert_align in rpr.findall(f"{W}vertAlign"):
        rpr.remove(vert_align)
    if hyperlink and rpr.find(f"{W}rStyle") is None:
        style = ET.SubElement(rpr, f"{W}rStyle")
        style.set(f"{W}val", "Hyperlink")
    if strong and rpr.find(f"{W}b") is None:
        ET.SubElement(rpr, f"{W}b")
    if emphasis and rpr.find(f"{W}i") is None:
        ET.SubElement(rpr, f"{W}i")
    return rpr if len(rpr) or rpr.attrib else None


def _base_run_properties(p: ET.Element) -> ET.Element | None:
    for r in p.findall(f"{W}r"):
        rpr = r.find(f"{W}rPr")
        if rpr is not None:
            return rpr
    return None


def _text_run(
    text: str,
    base_rpr: ET.Element | None,
    *,
    strong: bool,
    emphasis: bool,
    hyperlink: bool = False,
) -> ET.Element:
    r = ET.Element(f"{W}r")
    rpr = _clone_run_properties(
        base_rpr,
        strong=strong,
        emphasis=emphasis,
        hyperlink=hyperlink,
    )
    if rpr is not None:
        r.append(rpr)
    t = ET.SubElement(r, f"{W}t")
    if text[:1].isspace() or text[-1:].isspace():
        t.set(XML_SPACE, "preserve")
    t.text = text
    return r


def _docx_visible_text(text: str) -> str:
    text = re.sub(r"\^([^\s^]+)\^", r"\1", text)
    return re.sub(r"~([^\s~]+)~", r"\1", text)


def _append_ooxml_run(
    p: ET.Element,
    run: ET.Element,
    *,
    link_target: str | None,
    hyperlinks: HyperlinkRelationshipAllocator | None,
) -> None:
    if not link_target:
        p.append(run)
        return
    if hyperlinks is None:
        raise DocxTranslationError(
            f"cannot create hyperlink relationship for {link_target!r} in this DOCX part"
        )
    hyperlink = ET.Element(f"{W}hyperlink")
    hyperlink.set(f"{R}id", hyperlinks.add_external_hyperlink(link_target))
    hyperlink.append(run)
    p.append(hyperlink)


def _replace_image_metadata(p: ET.Element, unit: MarkdownTransferUnit) -> None:
    alt_text = unit.plain_text.strip()
    for index, docpr in enumerate(
        (element for element in p.iter() if element.tag in DRAWING_METADATA_ELEMENT_TAGS),
        start=1,
    ):
        if alt_text:
            name = alt_text if index == 1 else f"{alt_text} {index}"
            docpr.set(DRAWING_METADATA_NAME_ATTR, name)
            docpr.set(DRAWING_METADATA_DESCRIPTION_ATTR, alt_text)
        else:
            docpr.set(DRAWING_METADATA_NAME_ATTR, f"Drawing {index}")
            docpr.attrib.pop(DRAWING_METADATA_DESCRIPTION_ATTR, None)


def _has_cyrillic(value: str) -> bool:
    return bool(re.search(r"[А-Яа-яЁё]", value))


def _sanitize_drawing_metadata(root: ET.Element) -> bool:
    changed = False
    for index, docpr in enumerate(
        (element for element in root.iter() if element.tag in DRAWING_METADATA_ELEMENT_TAGS),
        start=1,
    ):
        name = str(docpr.get(DRAWING_METADATA_NAME_ATTR) or "")
        descr = str(docpr.get(DRAWING_METADATA_DESCRIPTION_ATTR) or "")
        title = str(docpr.get(DRAWING_METADATA_TITLE_ATTR) or "")
        if _has_cyrillic(name):
            docpr.set(DRAWING_METADATA_NAME_ATTR, f"Drawing {index}")
            changed = True
        if _has_cyrillic(descr):
            docpr.attrib.pop(DRAWING_METADATA_DESCRIPTION_ATTR, None)
            changed = True
        if _has_cyrillic(title):
            docpr.attrib.pop(DRAWING_METADATA_TITLE_ATTR, None)
            changed = True
    return changed


def _sanitize_drawing_metadata_parts(parts: dict[str, bytes]) -> None:
    for part_name in sorted(name for name in parts if DRAWING_METADATA_WORD_PART_RE.fullmatch(name)):
        source_xml = parts[part_name]
        root = ET.fromstring(source_xml)
        if _sanitize_drawing_metadata(root):
            parts[part_name] = serialize_xml(root, source_xml=source_xml)


def _parent_map(root: ET.Element) -> dict[ET.Element, ET.Element]:
    return {child: parent for parent in root.iter() for child in list(parent)}


def _body_child_for(
    paragraph: ET.Element,
    *,
    body: ET.Element,
    parents: dict[ET.Element, ET.Element],
) -> ET.Element:
    node = paragraph
    while (parent := parents.get(node)) is not None:
        if parent is body:
            return node
        node = parent
    raise DocxTranslationError("ignored source DOCX paragraph is not inside the document body")


def _remove_ignored_word_slots(root: ET.Element, ignored_slots: Sequence[IgnoredWordSlot]) -> None:
    if not ignored_slots:
        return
    body = root.find(f"{W}body")
    if body is None:
        raise DocxTranslationError("source DOCX has no word/body")
    parents = _parent_map(root)
    body_children: list[ET.Element] = []
    seen: set[int] = set()
    for ignored in ignored_slots:
        child = _body_child_for(ignored.slot.paragraph, body=body, parents=parents)
        identity = id(child)
        if identity not in seen:
            seen.add(identity)
            body_children.append(child)
    for child in body_children:
        body.remove(child)


def _replace_embedded_cover_data_uri(
    root: ET.Element,
    cover: MarkdownCoverImage | None,
) -> tuple[Diagnostic, ...]:
    instr_text_nodes = [
        node
        for node in root.iter(f"{W}instrText")
        if node.text and "INCLUDEPICTURE" in node.text and "data:image/" in node.text
    ]
    if not instr_text_nodes:
        return ()
    if cover is None:
        return (Diagnostic(
            "fatal",
            "docx-translate.cover-image-missing",
            "source DOCX contains an embedded cover data URI, but translated Markdown has no usable cover image.",
        ),)
    data_uri = cover.data_uri
    for node in instr_text_nodes:
        node.text = re.sub(
            r'data:image/[^";\s]+;base64,[A-Za-z0-9+/=]+',
            data_uri,
            node.text or "",
        )
    return (Diagnostic(
        "warning",
        "docx-translate.cover-image-replaced",
        f"replaced {len(instr_text_nodes)} embedded source cover image(s) with {cover.path.name}.",
    ),)


def _append_translated_inlines(
    p: ET.Element,
    unit: MarkdownTransferUnit,
    *,
    base_rpr: ET.Element | None,
    footnote_refs: Sequence[ET.Element],
    hyperlinks: HyperlinkRelationshipAllocator | None,
) -> None:
    footnote_cursor = 0
    for inline in unit.inlines:
        if isinstance(inline, TranslatedTextRun):
            for index, chunk in enumerate(_docx_visible_text(inline.text).split("\n")):
                if index:
                    br_run = ET.Element(f"{W}r")
                    br = ET.SubElement(br_run, f"{W}br")
                    del br
                    _append_ooxml_run(
                        p,
                        br_run,
                        link_target=inline.link_target,
                        hyperlinks=hyperlinks,
                    )
                if chunk:
                    _append_ooxml_run(
                        p,
                        _text_run(
                            chunk,
                            base_rpr,
                            strong=inline.strong,
                            emphasis=inline.emphasis,
                            hyperlink=inline.link_target is not None,
                        ),
                        link_target=inline.link_target,
                        hyperlinks=hyperlinks,
                    )
        elif isinstance(inline, FootnoteAnchor):
            if footnote_cursor >= len(footnote_refs):
                raise DocxTranslationError(
                    f"paragraph {unit.plain_text[:80]!r} needs more footnote reference runs than source DOCX has"
                )
            p.append(copy.deepcopy(footnote_refs[footnote_cursor]))
            footnote_cursor += 1
        else:
            assert_never(inline)
    if footnote_cursor != len(footnote_refs):
        raise DocxTranslationError(
            f"paragraph {unit.plain_text[:80]!r} used {footnote_cursor} footnote references but source has "
            f"{len(footnote_refs)}"
        )


def _replace_paragraph_text(
    p: ET.Element,
    unit: MarkdownTransferUnit,
    *,
    hyperlinks: HyperlinkRelationshipAllocator | None,
) -> None:
    if unit.kind == "image":
        _replace_image_metadata(p, unit)
        return
    if unit.kind == "thematic":
        if not normalize_transfer_text(word_paragraph_text(p)):
            base_rpr = _base_run_properties(p)
            for child in list(p):
                if child.tag != f"{W}pPr":
                    p.remove(child)
            p.append(_text_run(
                "***",
                base_rpr,
                strong=False,
                emphasis=False,
            ))
        return
    base_rpr = _base_run_properties(p)
    footnote_refs = [copy.deepcopy(r) for r in p.findall(f".//{W}footnoteReference/..")]
    drawing_runs = [
        copy.deepcopy(r)
        for r in p.findall(f"{W}r")
        if r.find(f".//{W}drawing") is not None or r.find(f".//{W}pict") is not None
    ]
    for child in list(p):
        if child.tag != f"{W}pPr":
            p.remove(child)
    for run in drawing_runs:
        p.append(run)
    _append_translated_inlines(
        p,
        unit,
        base_rpr=base_rpr,
        footnote_refs=footnote_refs,
        hyperlinks=hyperlinks,
    )


def _unit_has_hyperlink(unit: MarkdownTransferUnit) -> bool:
    return any(
        isinstance(inline, TranslatedTextRun) and inline.link_target
        for inline in unit.inlines
    )


def _relationship_refs(root: ET.Element) -> set[RelationshipId]:
    refs: set[RelationshipId] = set()
    for element in root.iter():
        for attr, value in element.attrib.items():
            if attr in RELATIONSHIP_REF_ATTRS and value:
                refs.add(str(value))
    return refs


def _rels_part_for(part_name: OoxmlPartName) -> OoxmlPartName:
    if "/" not in part_name:
        return f"_rels/{part_name}.rels"
    prefix, leaf = part_name.rsplit("/", 1)
    return f"{prefix}/_rels/{leaf}.rels"


def _prune_unreferenced_external_hyperlinks(
    parts: dict[str, bytes],
    part_name: OoxmlPartName,
) -> None:
    rels_part = _rels_part_for(part_name)
    if part_name not in parts or rels_part not in parts:
        return

    part_root = ET.fromstring(parts[part_name])
    rels_root = ET.fromstring(parts[rels_part])
    referenced_ids = _relationship_refs(part_root)
    changed = False
    for rel in list(rels_root.findall(f"{REL}Relationship")):
        if rel.get("Type") != HYPERLINK_REL_TYPE or rel.get("TargetMode") != "External":
            continue
        if rel.get("Id") in referenced_ids:
            continue
        rels_root.remove(rel)
        changed = True
    if changed:
        parts[rels_part] = serialize_relationships(rels_root, source_xml=parts[rels_part])


def _run_text(run: ET.Element) -> str:
    return "".join(t.text or "" for t in run.findall(f".//{W}t"))


def _footnote_marker_prefix(template_p: ET.Element | None) -> tuple[ET.Element, ...]:
    if template_p is None:
        return ()
    prefix: list[ET.Element] = []
    marker_seen = False
    for child in list(template_p):
        if child.tag == f"{W}pPr":
            continue
        if child.find(f".//{W}footnoteRef") is not None:
            marker_seen = True
            prefix.append(copy.deepcopy(child))
            continue
        if marker_seen and child.tag == f"{W}r" and not _run_text(child).strip():
            prefix.append(copy.deepcopy(child))
            continue
        break
    return tuple(prefix)


def _footnote_body_run_properties(template_p: ET.Element | None) -> ET.Element | None:
    if template_p is None:
        return None
    for run in template_p.findall(f"{W}r"):
        if run.find(f".//{W}footnoteRef") is not None:
            continue
        if not _run_text(run).strip():
            continue
        rpr = run.find(f"{W}rPr")
        if rpr is not None:
            return rpr
    return _base_run_properties(template_p)


def _footnote_id(note: ET.Element) -> str | None:
    raw = str(note.get(f"{W}id") or "")
    try:
        return raw if int(raw) > 0 else None
    except ValueError as exc:
        raise DocxTranslationError(f"source DOCX has non-numeric footnote id {raw!r}") from exc


def footnote_reference_ids_by_body_order(root: ET.Element) -> tuple[str, ...]:
    ids: list[str] = []
    for ref in root.findall(f".//{W}footnoteReference"):
        raw = str(ref.get(f"{W}id") or "")
        try:
            if int(raw) > 0:
                ids.append(raw)
        except ValueError as exc:
            raise DocxTranslationError(f"source DOCX has non-numeric body footnote reference {raw!r}") from exc
    return tuple(ids)


def _replace_footnotes(
    zf_parts: dict[str, bytes],
    translated: MarkdownTransferDocument,
    *,
    reference_ids: Sequence[str] | None = None,
) -> None:
    if not translated.footnotes or "word/footnotes.xml" not in zf_parts:
        return
    footnote_hyperlinks = (
        HyperlinkRelationshipAllocator(zf_parts, "word/_rels/footnotes.xml.rels")
        if any(_unit_has_hyperlink(unit) for footnote in translated.footnotes for unit in footnote)
        else None
    )
    root = ET.fromstring(zf_parts["word/footnotes.xml"])
    notes_by_id = {
        footnote_id: note
        for note in root.findall(f"{W}footnote")
        if (footnote_id := _footnote_id(note)) is not None
    }
    if reference_ids is None:
        reference_ids = tuple(notes_by_id)
    if len(notes_by_id) != len(translated.footnotes):
        raise DocxTranslationError(
            f"source DOCX has {len(notes_by_id)} footnote definitions; translated Markdown has "
            f"{len(translated.footnotes)}"
        )
    if len(reference_ids) != len(translated.footnotes):
        raise DocxTranslationError(
            f"source DOCX body has {len(reference_ids)} footnote references; translated Markdown has "
            f"{len(translated.footnotes)}"
        )
    if len(set(reference_ids)) != len(reference_ids):
        raise DocxTranslationError("source DOCX reuses a footnote reference id in the body")
    missing = [footnote_id for footnote_id in reference_ids if footnote_id not in notes_by_id]
    if missing:
        raise DocxTranslationError(
            "source DOCX body references missing footnote definition id(s): " + ", ".join(missing)
        )
    reference_id_set = set(reference_ids)
    unreferenced = [footnote_id for footnote_id in notes_by_id if footnote_id not in reference_id_set]
    if unreferenced:
        raise DocxTranslationError(
            "source DOCX has unreferenced footnote definition id(s): " + ", ".join(unreferenced)
        )
    notes = [notes_by_id[footnote_id] for footnote_id in reference_ids]
    for note, units in zip(notes, translated.footnotes, strict=True):
        template_p = note.find(f"{W}p")
        template_ppr = copy.deepcopy(template_p.find(f"{W}pPr")) if template_p is not None and template_p.find(f"{W}pPr") is not None else None
        marker_prefix = _footnote_marker_prefix(template_p)
        base_rpr = _footnote_body_run_properties(template_p)
        for child in list(note):
            if child.tag == f"{W}p":
                note.remove(child)
        for index, unit in enumerate(units):
            p = ET.Element(f"{W}p")
            if template_ppr is not None:
                p.append(copy.deepcopy(template_ppr))
            if index == 0:
                for run in marker_prefix:
                    p.append(copy.deepcopy(run))
            _append_translated_inlines(
                p,
                unit,
                base_rpr=base_rpr,
                footnote_refs=(),
                hyperlinks=footnote_hyperlinks,
            )
            note.append(p)
    if footnote_hyperlinks is not None:
        footnote_hyperlinks.save()
    zf_parts["word/footnotes.xml"] = serialize_xml(
        root,
        source_xml=zf_parts["word/footnotes.xml"],
    )
    _prune_unreferenced_external_hyperlinks(zf_parts, "word/footnotes.xml")


def _write_docx_parts(parts: dict[str, bytes], out: Path, *, member_order: Sequence[str]) -> None:
    try:
        seen_order = set(member_order)
        ordered_names = [
            name for name in member_order if name in parts
        ] + sorted(name for name in parts if name not in seen_order)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for name in ordered_names:
                info = zipfile.ZipInfo(name, date_time=FIXED_ZIP_TIMESTAMP)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o644 << 16
                zf.writestr(info, parts[name])
    except (OSError, zipfile.BadZipFile) as exc:
        raise DocxTranslationError(f"could not write DOCX package {out}") from exc


def _repair_unbound_relationship_prefixes(xml: bytes) -> bytes:
    declared = set(re.findall(rb"\sxmlns:([A-Za-z_][A-Za-z0-9_.-]*)=", xml))
    for prefix, local_name in sorted(set(UNBOUND_REL_ATTR_RE.findall(xml))):
        if prefix in declared or not prefix.startswith(b"ns"):
            continue
        xml = re.sub(
            rb"(?<![A-Za-z0-9_.-])" + prefix + rb":" + local_name + rb"=",
            b"r:" + local_name + b"=",
            xml,
        )
    return xml


def _dedupe_media_payloads(parts: dict[str, bytes]) -> None:
    groups: dict[str, list[str]] = {}
    for name, payload in parts.items():
        if name.startswith("word/media/"):
            groups.setdefault(hashlib.sha256(payload).hexdigest(), []).append(name)
    replacements: dict[str, str] = {}
    for names in groups.values():
        if len(names) < 2:
            continue
        canonical = sorted(names, key=_media_dedupe_sort_key)[0]
        for duplicate in names:
            if duplicate != canonical:
                replacements[duplicate] = canonical
    if not replacements:
        return
    for name in [part for part in parts if part.endswith(".rels")]:
        updated = _retarget_relationships(parts[name], rels_name=name, replacements=replacements)
        if updated != parts[name]:
            parts[name] = updated
    for duplicate in replacements:
        parts.pop(duplicate, None)


def _media_dedupe_sort_key(name: str) -> tuple[int, str]:
    leaf = name.rsplit("/", 1)[-1]
    return (1 if leaf.startswith("rId") else 0, name)


def _retarget_relationships(
    rels_xml: bytes,
    *,
    rels_name: str,
    replacements: dict[str, str],
) -> bytes:
    try:
        root = ET.fromstring(rels_xml)
    except ET.ParseError:
        return rels_xml
    source_part = _rels_source_part(rels_name)
    source_dir = posixpath.dirname(source_part)
    changed = False
    for rel in root.findall(f"{REL}Relationship"):
        target = rel.get("Target")
        if not target or rel.get("TargetMode") == "External":
            continue
        resolved = _resolve_relationship_target(source_part, target)
        replacement = replacements.get(resolved)
        if replacement is None:
            continue
        rel.set("Target", _relative_relationship_target(source_dir, replacement))
        changed = True
    if not changed:
        return rels_xml
    return serialize_relationships(root, source_xml=rels_xml)


def _rels_source_part(rels_name: str) -> str:
    if rels_name == "_rels/.rels":
        return ""
    if "/_rels/" not in rels_name or not rels_name.endswith(".rels"):
        return ""
    prefix, leaf = rels_name.split("/_rels/", 1)
    return f"{prefix}/{leaf.removesuffix('.rels')}"


def _resolve_relationship_target(source_part: str, target: str) -> str:
    path = target.lstrip("/")
    if target.startswith("/"):
        return posixpath.normpath(path)
    base = posixpath.dirname(source_part)
    return posixpath.normpath(posixpath.join(base, path))


def _relative_relationship_target(source_dir: str, target: str) -> str:
    if not source_dir:
        return target
    return posixpath.relpath(target, start=source_dir)


replace_paragraph_text = _replace_paragraph_text
unit_has_hyperlink = _unit_has_hyperlink
remove_ignored_word_slots = _remove_ignored_word_slots
sanitize_drawing_metadata = _sanitize_drawing_metadata
sanitize_drawing_metadata_parts = _sanitize_drawing_metadata_parts
replace_embedded_cover_data_uri = _replace_embedded_cover_data_uri
replace_footnotes = _replace_footnotes
write_docx_parts = _write_docx_parts
repair_unbound_relationship_prefixes = _repair_unbound_relationship_prefixes
dedupe_media_payloads = _dedupe_media_payloads
prune_unreferenced_external_hyperlinks = _prune_unreferenced_external_hyperlinks
