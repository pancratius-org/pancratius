# import-pure: no filesystem mutation
"""Shared OOXML helpers.

The importer reads paragraph-level Word signals that Pandoc Markdown cannot
carry. The translated-DOCX transfer also serializes edited XML parts, so this
module owns the namespace registration instead of relying on import-time side
effects from one DOCX command.
"""

from __future__ import annotations

import io
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterable, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from urllib.parse import quote, unquote, urlsplit

MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
PIC_NS = "http://schemas.openxmlformats.org/drawingml/2006/picture"
XML_NS = "http://www.w3.org/XML/1998/namespace"
HYPERLINK_REL_TYPE = f"{R_NS}/hyperlink"
EMBED_REL_TYPES = frozenset({
    f"{R_NS}/audio",
    f"{R_NS}/image",
    f"{R_NS}/oleObject",
    f"{R_NS}/package",
    f"{R_NS}/video",
})
REL = f"{{{REL_NS}}}"
R = f"{{{R_NS}}}"
W = f"{{{W_NS}}}"
WP = f"{{{WP_NS}}}"
PIC = f"{{{PIC_NS}}}"
XML_SPACE = f"{{{XML_NS}}}space"
DRAWING_METADATA_NAME_ATTR = "name"
DRAWING_METADATA_DESCRIPTION_ATTR = "descr"
DRAWING_METADATA_TITLE_ATTR = "title"
DRAWING_METADATA_ATTRS = (
    DRAWING_METADATA_NAME_ATTR,
    DRAWING_METADATA_DESCRIPTION_ATTR,
    DRAWING_METADATA_TITLE_ATTR,
)
DRAWING_METADATA_ELEMENT_TAGS = frozenset({f"{WP}docPr", f"{PIC}cNvPr"})
DRAWING_METADATA_WORD_PART_RE = re.compile(
    r"^word/(document|header\d+|footer\d+|footnotes|endnotes)\.xml$"
)


@dataclass(frozen=True)
class DocxParagraphMeta:
    text: str
    align: str
    style: str
    bold: bool
    italic: bool

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


@dataclass(frozen=True, slots=True)
class NamespaceBinding:
    prefix: str
    uri: str


@dataclass(frozen=True, slots=True)
class OoxmlRelationship:
    rel_id: str
    rel_type: str
    target: str
    target_mode: str | None
    resolved_target: str | None


@dataclass(frozen=True, slots=True)
class OoxmlRelationshipRef:
    attr_name: str
    rel_id: str


@dataclass(frozen=True, slots=True)
class OoxmlRelationshipRead:
    source_part: str
    relationships: dict[str, OoxmlRelationship]
    issues: tuple[str, ...]


class OoxmlRelationshipError(ValueError):
    """An OOXML relationship path cannot be trusted."""


COMMON_NAMESPACES: tuple[NamespaceBinding, ...] = (
    NamespaceBinding("wpc", "http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas"),
    NamespaceBinding("cx", "http://schemas.microsoft.com/office/drawing/2014/chartex"),
    NamespaceBinding("cx1", "http://schemas.microsoft.com/office/drawing/2015/9/8/chartex"),
    NamespaceBinding("cx2", "http://schemas.microsoft.com/office/drawing/2015/10/21/chartex"),
    NamespaceBinding("cx3", "http://schemas.microsoft.com/office/drawing/2016/5/9/chartex"),
    NamespaceBinding("cx4", "http://schemas.microsoft.com/office/drawing/2016/5/10/chartex"),
    NamespaceBinding("cx5", "http://schemas.microsoft.com/office/drawing/2016/5/11/chartex"),
    NamespaceBinding("cx6", "http://schemas.microsoft.com/office/drawing/2016/5/12/chartex"),
    NamespaceBinding("cx7", "http://schemas.microsoft.com/office/drawing/2016/5/13/chartex"),
    NamespaceBinding("cx8", "http://schemas.microsoft.com/office/drawing/2016/5/14/chartex"),
    NamespaceBinding("mc", MC_NS),
    NamespaceBinding("aink", "http://schemas.microsoft.com/office/drawing/2016/ink"),
    NamespaceBinding("am3d", "http://schemas.microsoft.com/office/drawing/2017/model3d"),
    NamespaceBinding("o", "urn:schemas-microsoft-com:office:office"),
    NamespaceBinding("oel", "http://schemas.microsoft.com/office/2019/extlst"),
    NamespaceBinding("r", "http://schemas.openxmlformats.org/officeDocument/2006/relationships"),
    NamespaceBinding("m", "http://schemas.openxmlformats.org/officeDocument/2006/math"),
    NamespaceBinding("v", "urn:schemas-microsoft-com:vml"),
    NamespaceBinding("wp14", "http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing"),
    NamespaceBinding("wp", WP_NS),
    NamespaceBinding("w10", "urn:schemas-microsoft-com:office:word"),
    NamespaceBinding("w", W_NS),
    NamespaceBinding("w14", "http://schemas.microsoft.com/office/word/2010/wordml"),
    NamespaceBinding("w15", "http://schemas.microsoft.com/office/word/2012/wordml"),
    NamespaceBinding("w16cex", "http://schemas.microsoft.com/office/word/2018/wordml/cex"),
    NamespaceBinding("w16cid", "http://schemas.microsoft.com/office/word/2016/wordml/cid"),
    NamespaceBinding("w16", "http://schemas.microsoft.com/office/word/2018/wordml"),
    NamespaceBinding("w16du", "http://schemas.microsoft.com/office/word/2023/wordml/word16du"),
    NamespaceBinding("w16sdtdh", "http://schemas.microsoft.com/office/word/2020/wordml/sdtdatahash"),
    NamespaceBinding("w16sdtfl", "http://schemas.microsoft.com/office/word/2024/wordml/sdtformatlock"),
    NamespaceBinding("w16se", "http://schemas.microsoft.com/office/word/2015/wordml/symex"),
    NamespaceBinding("wpg", "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup"),
    NamespaceBinding("wpi", "http://schemas.microsoft.com/office/word/2010/wordprocessingInk"),
    NamespaceBinding("wne", "http://schemas.microsoft.com/office/word/2006/wordml"),
    NamespaceBinding("wps", "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"),
    NamespaceBinding("a", "http://schemas.openxmlformats.org/drawingml/2006/main"),
    NamespaceBinding("a14", "http://schemas.microsoft.com/office/drawing/2010/main"),
    NamespaceBinding("pic", PIC_NS),
)

_RESERVED_ET_PREFIX_RE = re.compile(r"ns\d+$")
_XML_DECL_RE = re.compile(rb"^\s*<\?xml[^>]*\?>")
_PREFIX_VALUED_ATTRS = frozenset(
    {
        "Ignorable",
        "MustUnderstand",
        "ProcessContent",
        "PreserveElements",
        "PreserveAttributes",
        "Requires",
    }
)


def _w_val(el: ET.Element | None) -> str:
    if el is None:
        return ""
    return str(el.get(f"{W}val") or "")


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _run_prop_enabled(el: ET.Element | None) -> bool:
    if el is None:
        return False
    val = el.get(f"{W}val")
    return val not in {"0", "false", "False", "off"}


def read_docx_paragraph_meta(docx: Path) -> list[DocxParagraphMeta]:
    """Read paragraph metadata that Markdown cannot carry."""
    with zipfile.ZipFile(docx) as zf:
        root = ET.fromstring(zf.read("word/document.xml"))

    paras: list[DocxParagraphMeta] = []
    for p in root.iter(f"{W}p"):
        text_parts: list[str] = []
        for el in p.iter():
            if el.tag == f"{W}t":
                text_parts.append(el.text or "")
            elif el.tag in {f"{W}br", f"{W}cr"}:
                text_parts.append("\n")
            elif el.tag == f"{W}tab":
                text_parts.append("\t")

        ppr = p.find(f"{W}pPr")
        style = _w_val(ppr.find(f"{W}pStyle") if ppr is not None else None)
        align = _w_val(ppr.find(f"{W}jc") if ppr is not None else None)
        bold = any(_run_prop_enabled(el) for el in p.findall(f".//{W}b"))
        italic = any(_run_prop_enabled(el) for el in p.findall(f".//{W}i"))
        paras.append(DocxParagraphMeta(
            text="".join(text_parts).strip(),
            align=align,
            style=style,
            bold=bold,
            italic=italic,
        ))
    return paras


def namespace_bindings(xml: bytes) -> tuple[NamespaceBinding, ...]:
    bindings: list[NamespaceBinding] = []
    for _event, (prefix, uri) in ET.iterparse(io.BytesIO(xml), events=("start-ns",)):
        bindings.append(NamespaceBinding(prefix, uri))
    return tuple(bindings)


def relationship_source_part(rels_name: str) -> str:
    if rels_name == "_rels/.rels":
        return ""
    if rels_name.startswith("_rels/") and rels_name.endswith(".rels"):
        leaf = rels_name.removeprefix("_rels/")
        if "/" in leaf:
            raise OoxmlRelationshipError(f"unexpected relationships part path: {rels_name}")
        return leaf.removesuffix(".rels")
    if "/_rels/" not in rels_name or not rels_name.endswith(".rels"):
        raise OoxmlRelationshipError(f"unexpected relationships part path: {rels_name}")
    prefix, leaf = rels_name.split("/_rels/", 1)
    if "/" in leaf:
        raise OoxmlRelationshipError(f"unexpected relationships part path: {rels_name}")
    return f"{prefix}/{leaf.removesuffix('.rels')}"


def resolve_relationship_target(source_part: str, target: str) -> str:
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc:
        raise OoxmlRelationshipError(
            f"relationship target from {source_part or '/'} is external without TargetMode=External: {target}"
        )
    path = unquote(parsed.path)
    if not path:
        raise OoxmlRelationshipError(f"relationship target from {source_part or '/'} is empty")
    if path.startswith("/"):
        resolved = posixpath.normpath(path.lstrip("/"))
    else:
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(source_part), path))
    if resolved in {".", ".."} or resolved.startswith("../"):
        raise OoxmlRelationshipError(
            f"relationship target from {source_part or '/'} escapes the DOCX package: {target}"
        )
    return resolved


def relative_relationship_target(source_part: str, target_part: str) -> str:
    source_dir = posixpath.dirname(source_part)
    if not source_dir:
        relative_target = target_part
    else:
        relative_target = posixpath.relpath(target_part, start=source_dir)
    return quote(relative_target, safe="/:@!$&'()*+,;=")


def relationships_part_for(source_part: str) -> str:
    if "/" in source_part:
        prefix, leaf = source_part.rsplit("/", 1)
        return f"{prefix}/_rels/{leaf}.rels"
    return f"_rels/{source_part}.rels"


def read_ooxml_relationships(
    root: ET.Element,
    rels_name: str,
    package_part_names: set[str],
) -> OoxmlRelationshipRead:
    try:
        source_part = relationship_source_part(rels_name)
    except OoxmlRelationshipError as exc:
        return OoxmlRelationshipRead("", {}, (str(exc),))
    if source_part and source_part not in package_part_names:
        return OoxmlRelationshipRead(
            source_part,
            {},
            (f"{rels_name} has no source part {source_part}",),
        )

    issues: list[str] = []
    relationships: dict[str, OoxmlRelationship] = {}
    for rel in root.findall(f"{REL}Relationship"):
        rel_id = rel.get("Id")
        if rel_id is None:
            issues.append(f"{rels_name} has a relationship without Id")
            continue
        if rel_id in relationships:
            issues.append(f"{rels_name} has duplicate relationship Id {rel_id}")
            continue
        rel_type = rel.get("Type")
        if rel_type is None:
            issues.append(f"{rels_name} relationship {rel_id} has no Type")
            continue
        target = rel.get("Target")
        if not target:
            issues.append(f"{rels_name} relationship {rel_id} is missing Target")
            continue
        target_mode = rel.get("TargetMode")
        if target_mode not in {None, "Internal", "External"}:
            issues.append(
                f"{rels_name} relationship {rel_id} has invalid TargetMode {target_mode!r}"
            )
            continue
        if target_mode == "External":
            relationships[rel_id] = OoxmlRelationship(
                rel_id=rel_id,
                rel_type=rel_type,
                target=target,
                target_mode=target_mode,
                resolved_target=None,
            )
            continue
        try:
            resolved = resolve_relationship_target(source_part, target)
        except OoxmlRelationshipError as exc:
            issues.append(f"{rels_name} relationship {rel_id}: {exc}")
            continue
        if resolved not in package_part_names:
            issues.append(
                f"{rels_name} relationship {rel_id} targets missing package part "
                f"{target!r} (resolved as {resolved!r})"
            )
            continue
        relationships[rel_id] = OoxmlRelationship(
            rel_id=rel_id,
            rel_type=rel_type,
            target=target,
            target_mode=target_mode,
            resolved_target=resolved,
        )
    return OoxmlRelationshipRead(source_part, relationships, tuple(issues))


def office_relationship_refs(root: ET.Element) -> tuple[OoxmlRelationshipRef, ...]:
    prefix = f"{{{R_NS}}}"
    return tuple(
        OoxmlRelationshipRef(attr.removeprefix(prefix), value)
        for element in root.iter()
        for attr, value in element.attrib.items()
        if attr.startswith(prefix)
    )


def register_namespaces(bindings: Iterable[NamespaceBinding] = ()) -> None:
    seen: set[tuple[str, str]] = set()
    for binding in (*COMMON_NAMESPACES, *tuple(bindings)):
        key = (binding.prefix, binding.uri)
        if key in seen:
            continue
        seen.add(key)
        if binding.prefix == "xml" or _RESERVED_ET_PREFIX_RE.fullmatch(binding.prefix):
            continue
        ET.register_namespace(binding.prefix, binding.uri)


def serialize_xml(
    root: ET.Element,
    *,
    source_xml: bytes | None = None,
    bindings: Iterable[NamespaceBinding] = (),
) -> bytes:
    snapshot = _namespace_registry_snapshot()
    try:
        source_bindings = namespace_bindings(source_xml) if source_xml is not None else ()
        all_bindings = (*COMMON_NAMESPACES, *source_bindings, *tuple(bindings))
        register_namespaces(all_bindings)
        payload = ET.tostring(root, encoding="UTF-8", xml_declaration=True)
        missing = _missing_prefix_value_bindings(root, payload, all_bindings)
        if not missing:
            return payload
        return _inject_namespace_declarations(payload, missing)
    finally:
        _restore_namespace_registry(snapshot)


def serialize_relationships(root: ET.Element, *, source_xml: bytes | None = None) -> bytes:
    return serialize_xml(
        root,
        source_xml=source_xml,
        bindings=(NamespaceBinding("", REL_NS),),
    )


def _missing_prefix_value_bindings(
    root: ET.Element,
    payload: bytes,
    bindings: Iterable[NamespaceBinding],
) -> tuple[NamespaceBinding, ...]:
    declared = {match.decode("ascii") for match in re.findall(rb"\sxmlns:([A-Za-z_][\w.-]*)=", payload)}
    binding_by_prefix = {binding.prefix: binding.uri for binding in bindings if binding.prefix}
    missing: list[NamespaceBinding] = []
    for prefix in sorted(_prefix_value_references(root)):
        if prefix in declared:
            continue
        uri = binding_by_prefix.get(prefix)
        if uri is not None:
            missing.append(NamespaceBinding(prefix, uri))
    return tuple(missing)


def _prefix_value_references(root: ET.Element) -> set[str]:
    out: set[str] = set()
    for elem in root.iter():
        for attr, value in elem.attrib.items():
            local = attr.rsplit("}", 1)[-1] if attr.startswith("{") else attr
            if local not in _PREFIX_VALUED_ATTRS:
                continue
            for token in value.split():
                prefix = token.split(":", 1)[0]
                if prefix:
                    out.add(prefix)
    return out


def _inject_namespace_declarations(
    payload: bytes,
    missing: Iterable[NamespaceBinding],
) -> bytes:
    start = 0
    if match := _XML_DECL_RE.match(payload):
        start = match.end()
    marker = payload.find(b">", start)
    if marker < 0:
        return payload
    attrs = b"".join(
        f' xmlns:{binding.prefix}="{binding.uri}"'.encode()
        for binding in missing
    )
    return payload[:marker] + attrs + payload[marker:]


def _namespace_registry_snapshot() -> dict[str, str] | None:
    namespace_map = _namespace_registry()
    return None if namespace_map is None else dict(namespace_map)


def _restore_namespace_registry(snapshot: dict[str, str] | None) -> None:
    namespace_map = _namespace_registry()
    if namespace_map is None or snapshot is None:
        return
    namespace_map.clear()
    namespace_map.update(snapshot)


def _namespace_registry() -> MutableMapping[str, str] | None:
    raw = getattr(ET, "_namespace_map", None)
    return cast("MutableMapping[str, str]", raw) if isinstance(raw, dict) else None
