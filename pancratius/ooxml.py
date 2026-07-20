# import-pure: no filesystem mutation
"""Shared OOXML package and namespace helpers.

The canonical reader and translated-DOCX transfer both need exact namespace and
relationship handling.  This module owns those mechanics without assigning
document or product semantics.
"""

from __future__ import annotations

import io
import posixpath
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable, MutableMapping
from dataclasses import dataclass
from typing import cast
from urllib.parse import quote, unquote, urlsplit
from xml.sax.saxutils import quoteattr

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
MC_ALTERNATE_CONTENT = f"{{{MC_NS}}}AlternateContent"
MC_FALLBACK = f"{{{MC_NS}}}Fallback"
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


@dataclass(frozen=True, slots=True)
class NamespaceBinding:
    prefix: str
    uri: str


@dataclass(frozen=True, slots=True)
class PrefixValueReference:
    """One lexical namespace reference resolved where it appears."""

    prefix: str
    uri: str | None


@dataclass(frozen=True, slots=True)
class ElementNamespaceContext:
    """The lexical namespace scope captured at one parsed element."""

    element: ET.Element
    bindings: tuple[NamespaceBinding, ...]

    def resolve(self, prefix: str) -> str | None:
        return next(
            (binding.uri for binding in self.bindings if binding.prefix == prefix),
            None,
        )


@dataclass(frozen=True, slots=True)
class ParsedOoxml:
    """One parsed XML tree with immutable lexical scope provenance."""

    root: ET.Element
    bindings: tuple[NamespaceBinding, ...]
    element_contexts: tuple[ElementNamespaceContext, ...]

    def references_in(self, root: ET.Element | None = None) -> tuple[PrefixValueReference, ...]:
        retained = self.root if root is None else root
        contexts = {context.element: context for context in self.element_contexts}
        return tuple(
            PrefixValueReference(
                prefix,
                contexts[element].resolve(prefix) if element in contexts else None,
            )
            for element in retained.iter()
            for prefix in _prefix_value_references_in_attributes(element)
        )


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


class OoxmlNamespaceError(ValueError):
    """OOXML carries a lexical namespace reference that cannot be resolved."""


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
_PREFIX_LIST_ATTRS = frozenset({"Ignorable", "MustUnderstand", "Requires"})
_QNAME_LIST_ATTRS = frozenset(
    {"ProcessContent", "PreserveElements", "PreserveAttributes"}
)


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_xml(xml: bytes) -> ParsedOoxml:
    """Parse once, retaining scoped meanings of lexical prefix-valued attributes."""
    bindings: list[NamespaceBinding] = []
    contexts: list[ElementNamespaceContext] = []
    pending: dict[str, str] = {}
    scopes: list[dict[str, str]] = [{"xml": XML_NS}]
    root: ET.Element | None = None
    events = ET.iterparse(io.BytesIO(xml), events=("start-ns", "start", "end"))
    for event, value in events:
        if event == "start-ns":
            prefix, uri = value
            bindings.append(NamespaceBinding(prefix, uri))
            pending[prefix] = uri
            continue
        if event == "start":
            element = cast("ET.Element", value)
            if root is None:
                root = element
            scope = dict(scopes[-1])
            scope.update(pending)
            pending.clear()
            scopes.append(scope)
            if _prefix_value_references_in_attributes(element):
                contexts.append(
                    ElementNamespaceContext(
                        element,
                        tuple(
                            NamespaceBinding(prefix, uri)
                            for prefix, uri in scope.items()
                        ),
                    )
                )
            continue
        if len(scopes) > 1:
            scopes.pop()
    if root is None:
        raise ET.ParseError("no element found")
    return ParsedOoxml(root, tuple(bindings), tuple(contexts))


def unresolved_prefix_value_references(xml: bytes) -> set[str]:
    """Lexical namespace prefixes that are not in scope where referenced."""
    return {
        reference.prefix
        for reference in prefix_value_references_in_xml(xml)
        if reference.uri is None
    }


def prefix_value_references_in_xml(xml: bytes) -> tuple[PrefixValueReference, ...]:
    """Resolve lexical namespace references against their element scopes."""
    return parse_xml(xml).references_in()


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
    for binding in (*COMMON_NAMESPACES, *tuple(bindings)):
        if binding.prefix == "xml" or _RESERVED_ET_PREFIX_RE.fullmatch(binding.prefix):
            continue
        ET.register_namespace(binding.prefix, binding.uri)


def serialize_xml(
    tree: ParsedOoxml | ET.Element,
    *,
    bindings: Iterable[NamespaceBinding] = (),
) -> bytes:
    supplied_bindings = (*COMMON_NAMESPACES, *tuple(bindings))
    if isinstance(tree, ParsedOoxml):
        root = tree.root
        source_bindings = tree.bindings
        references = tree.references_in()
    else:
        root = tree
        source_bindings = ()
        references = tuple(
            PrefixValueReference(prefix, None)
            for prefix in prefix_value_references(root)
        )
    desired = _required_prefix_value_bindings(
        references,
        supplied_bindings=supplied_bindings,
    )
    snapshot = _namespace_registry_snapshot()
    try:
        # Canonical/supplied bindings win for element names. Lexical aliases
        # are closed separately below, so they cannot rename serialized QNames.
        lexical_prefixes = {binding.prefix for binding in desired}
        registry_source_bindings = tuple(
            binding
            for binding in source_bindings
            if binding.prefix not in lexical_prefixes
        )
        all_bindings = (*registry_source_bindings, *supplied_bindings)
        register_namespaces(all_bindings)
        payload = ET.tostring(root, encoding="UTF-8", xml_declaration=True)
        return _close_prefix_value_references(payload, desired)
    finally:
        _restore_namespace_registry(snapshot)


def serialize_relationships(
    tree: ParsedOoxml | ET.Element,
) -> bytes:
    return serialize_xml(
        tree,
        bindings=(NamespaceBinding("", REL_NS),),
    )


def _required_prefix_value_bindings(
    references: Iterable[PrefixValueReference],
    *,
    supplied_bindings: Iterable[NamespaceBinding],
) -> tuple[NamespaceBinding, ...]:
    references_by_prefix: dict[str, set[str | None]] = {}
    for reference in references:
        references_by_prefix.setdefault(reference.prefix, set()).add(reference.uri)

    supplied = {
        binding.prefix: binding.uri
        for binding in supplied_bindings
        if binding.prefix
    }
    desired: list[NamespaceBinding] = []
    unresolved: list[str] = []
    for prefix, resolutions in sorted(references_by_prefix.items()):
        resolved = {uri for uri in resolutions if uri is not None}
        if len(resolved) > 1:
            raise OoxmlNamespaceError(
                f"namespace prefix value {prefix!r} has conflicting meanings"
            )
        uri = next(iter(resolved), None)
        if None in resolutions:
            repair = supplied.get(prefix)
            if repair is None or (uri is not None and repair != uri):
                unresolved.append(prefix)
                continue
            uri = repair
        if uri is not None:
            desired.append(NamespaceBinding(prefix, uri))
    if unresolved:
        raise OoxmlNamespaceError(
            "unresolved namespace prefix value(s): " + ", ".join(unresolved)
        )
    return tuple(desired)


def _close_prefix_value_references(
    payload: bytes,
    desired: Iterable[NamespaceBinding],
) -> bytes:
    desired_by_prefix = {binding.prefix: binding for binding in desired}
    missing: set[str] = set()
    unknown: set[str] = set()
    changed: set[str] = set()
    for reference in prefix_value_references_in_xml(payload):
        expected = desired_by_prefix.get(reference.prefix)
        if expected is None:
            unknown.add(reference.prefix)
        elif reference.uri is None:
            missing.add(reference.prefix)
        elif reference.uri != expected.uri:
            changed.add(reference.prefix)
    if unknown:
        raise OoxmlNamespaceError(
            "unresolved namespace prefix value(s): " + ", ".join(sorted(unknown))
        )
    if changed:
        raise OoxmlNamespaceError(
            "namespace prefix value meaning changed during serialization: "
            + ", ".join(sorted(changed))
        )
    return _inject_namespace_declarations(
        payload,
        tuple(desired_by_prefix[prefix] for prefix in sorted(missing)),
    )


def prefix_value_references(root: ET.Element) -> set[str]:
    """Prefixes referenced lexically by markup-compatibility attributes."""
    out: set[str] = set()
    for elem in root.iter():
        out.update(_prefix_value_references_in_attributes(elem))
    return out


def _prefix_value_references_in_attributes(element: ET.Element) -> set[str]:
    out: set[str] = set()
    for attr, value in element.attrib.items():
        if attr.startswith(f"{{{MC_NS}}}"):
            local = attr.removeprefix(f"{{{MC_NS}}}")
        elif attr == "Requires" and element.tag == f"{{{MC_NS}}}Choice":
            local = attr
        else:
            continue
        if local not in _PREFIX_LIST_ATTRS | _QNAME_LIST_ATTRS:
            continue
        for token in value.split():
            if local in _QNAME_LIST_ATTRS and ":" not in token:
                continue
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
        f" xmlns:{binding.prefix}={quoteattr(binding.uri)}".encode()
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
