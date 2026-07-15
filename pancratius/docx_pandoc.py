"""Pandoc anti-corruption layer for DOCX packages.

The source aggregate owns authored semantics. This adapter projects only the
Word story parts Pandoc reads into its poorer vocabulary, then invokes Pandoc.
"""

from __future__ import annotations

import json
import re
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, assert_never, cast

from pancratius import docx_source, ooxml
from pancratius.ooxml import W
from pancratius.pandoc import pandoc_argv0

PANDOC_TIMEOUT_SECONDS = 300

# Source-anchor vocabulary. The projection tags every content `w:p` with a
# bookmark named for its ordinal; Pandoc's docx reader surfaces each as an
# anchor `Span` INSIDE whatever structure the paragraph lands in, so the
# adapter reads provenance off every leaf instead of reconstructing it.
# Pandoc prunes bookmarks no link references, so the projection also appends
# farm paragraphs of internal links (dropped again by the adapter).

# A bookmark identifier the projection mints: `pansrc<ordinal>`.
type SourceAnchorName = str
# One raw Pandoc `{"t": …, "c": …}` JSON node / an inline sequence of them
# (payload shapes vary by kind, so members stay untyped).
type PandocNode = dict[str, Any]
type PandocInlines = list[Any]
type PandocBlocks = list[Any]
# A farm link's target: `#pansrc<ordinal>`, or a surviving foreign bookmark /
# generated identifier Pandoc rewrote the link to.
type FarmLinkTarget = str
type FarmTargets = list[FarmLinkTarget]
type AnchorAlias = str

SOURCE_ANCHOR_PREFIX = "pansrc"
_SOURCE_ANCHOR_RE = re.compile(rf"^{SOURCE_ANCHOR_PREFIX}(\d+)$")
# Each farm chunk carries its own reserved bookmark plus a content-bearing
# self-link to it: the bookmark survives Pandoc (it is referenced), is never a
# heading (so never folded/rewritten), and identifies the chunk unambiguously —
# even when every ordinal link in the chunk was rewritten to a heading id.
_FARM_MARKER_PREFIX = f"{SOURCE_ANCHOR_PREFIX}farm"
_FARM_MARKER_RE = re.compile(rf"^{_FARM_MARKER_PREFIX}\d+$")
_ANCHOR_BOOKMARK_ID_BASE = 500_000  # floor; real allocation starts above the document's own ids
_ANCHOR_FARM_CHUNK = 800            # arbitrary bound so no farm paragraph grows degenerate


def source_anchor_name(ordinal: docx_source.SourceOrdinal) -> SourceAnchorName:
    return f"{SOURCE_ANCHOR_PREFIX}{ordinal}"


def source_anchor_ordinal(name: str) -> docx_source.SourceOrdinal | None:
    """The ordinal carried by a source-anchor identifier, else None."""
    m = _SOURCE_ANCHOR_RE.match(name)
    return int(m.group(1)) if m else None


def anchored_ordinals(source: docx_source.DocxSourceDocument) -> list[docx_source.SourceOrdinal]:
    """The ordinals the projection anchors, in farm order: content paragraphs
    only. Anchoring empty or non-text paragraphs perturbs how Pandoc reads them
    (an image-only `w:p` stops converting as a Figure), and only content
    ordinals carry reading/lineation truth."""
    return [
        int(p.ordinal)
        for p in source.paragraphs
        if p.disposition is docx_source.ParagraphDisposition.CONTENT
    ]


_FARM_LINK_LABEL = "."


def as_node(value: object) -> PandocNode | None:
    """View an opaque value as a Pandoc `{"t": …, "c": …}` node when it is a dict."""
    return cast("dict[str, Any]", value) if isinstance(value, dict) else None


def split_inline_anchors(
    nodes: PandocInlines,
    aliases: Mapping[str, docx_source.SourceOrdinal] | None = None,
) -> tuple[list[docx_source.SourceOrdinal], PandocInlines]:
    """Split source-anchor Spans out of a raw Pandoc inline list.

    An anchor is empty by construction (its bookmarkStart/End are adjacent), so
    the node is dropped whole. `aliases` names foreign anchors Pandoc kept in
    place of ours (a pre-existing `OLE_LINK`/`_Toc` bookmark at the same spot) —
    such a Span stays in the stream (real links may target it) but claims its
    ordinal. The separator Pandoc keeps before the trailing anchor goes with
    it: Pandoc never emits paragraph-trailing whitespace or breaks on its own,
    so trailing `Space`/`SoftBreak`/`LineBreak` nodes left after extraction are
    injection residue, not content."""
    ordinals: list[docx_source.SourceOrdinal] = []
    cleaned: PandocInlines = []
    for node in nodes:
        nd = as_node(node)
        if nd is not None and nd.get("t") == "Span":
            c = nd.get("c")
            if isinstance(c, list) and len(c) == 2 and isinstance(c[0], list) and c[0]:
                ident = str(c[0][0])
                if (ordinal := source_anchor_ordinal(ident)) is not None:
                    ordinals.append(ordinal)
                    continue
                if aliases and (aliased := aliases.get(ident)) is not None:
                    ordinals.append(aliased)
        cleaned.append(node)
    if ordinals:
        while cleaned and (nd := as_node(cleaned[-1])) is not None and nd.get("t") in {
            "Space",
            "SoftBreak",
            "LineBreak",
        }:
            cleaned.pop()
    return ordinals, cleaned


def farm_link_targets(block: object) -> FarmTargets | None:
    """The ordinal-position targets of one anchor-farm paragraph, or None for
    real content.

    A farm paragraph identifies itself by its reserved `pansrcfarm<k>` marker —
    a bookmark Span the chunk's own self-link keeps alive through Pandoc. The
    marker is authoritative: it survives even when every ordinal link in the
    chunk was rewritten to a heading id, and no authored paragraph can carry it
    (the injection rejects documents using the reserved namespace). The
    self-link's target is plumbing and is excluded from the returned targets."""
    nd = as_node(block)
    if nd is None or nd.get("t") not in {"Para", "Plain"}:
        return None
    inlines = nd.get("c")
    if not isinstance(inlines, list) or not inlines:
        return None
    marked = any(
        (span := as_node(raw)) is not None
        and span.get("t") == "Span"
        and isinstance(c := span.get("c"), list)
        and len(c) == 2
        and isinstance(c[0], list)
        and c[0]
        and _FARM_MARKER_RE.match(str(c[0][0]))
        for raw in inlines
    )
    if not marked:
        return None
    targets: FarmTargets = []
    for raw in inlines:
        link = as_node(raw)
        if link is None or link.get("t") != "Link":
            continue
        payload = link.get("c")
        if not isinstance(payload, list) or len(payload) != 3:
            continue
        target = payload[2]
        if not isinstance(target, list) or not target:
            continue
        value = str(target[0])
        if _FARM_MARKER_RE.match(value.removeprefix("#")):
            continue
        targets.append(value)
    return targets


def source_anchor_aliases(
    farm_targets: Sequence[FarmLinkTarget],
    source: docx_source.DocxSourceDocument,
) -> dict[AnchorAlias, docx_source.SourceOrdinal]:
    """Recover source provenance from the farm's rewritten link targets.

    Pandoc may retain an existing OLE_LINK/_Toc bookmark or fold a heading's
    bookmark into its generated identifier. Farm links are authored in ordinal
    order, so a link's position names its ordinal and a rewritten target names
    the surviving anchor that carries it."""
    expected = anchored_ordinals(source)
    if len(farm_targets) != len(expected):
        raise ValueError(
            f"anchor farm carries {len(farm_targets)} links for {len(expected)} anchored "
            f"paragraphs — the Pandoc anchor contract moved; provenance cannot be trusted"
        )
    out: dict[AnchorAlias, docx_source.SourceOrdinal] = {}
    for ordinal, target in zip(expected, farm_targets, strict=True):
        ident = target.removeprefix("#")
        carried = source_anchor_ordinal(ident)
        if carried is None:
            if ident in out and out[ident] != ordinal:
                raise ValueError(
                    f"anchor alias {ident!r} identifies source ordinals "
                    f"{out[ident]} and {ordinal}; provenance cannot be trusted"
                )
            out[ident] = ordinal
        # carried != ordinal: Pandoc consumed this ordinal's bookmark and redirected
        # the link to a surviving anchor. Positions stay aligned (the link exists),
        # the ordinal stays unclaimed, and the provenance diagnostic surfaces it.
    return out


def strip_source_anchors(blocks: PandocBlocks) -> PandocBlocks:
    """The AST's content blocks with the projection's anchor apparatus removed:
    farm paragraphs dropped, anchor Spans and their residue stripped from
    TOP-LEVEL `Para`/`Plain` inlines (anchors inside containers survive; today's
    consumers read only top-level paragraphs). For raw-AST consumers that don't
    need ordinals; the adapter extracts the anchors itself instead."""
    out: PandocBlocks = []
    for block in blocks:
        if farm_link_targets(block) is not None:
            continue
        nd = as_node(block)
        if nd is not None and nd.get("t") in {"Para", "Plain"} and isinstance(nd.get("c"), list):
            _ordinals, cleaned = split_inline_anchors(nd["c"])
            out.append({"t": nd["t"], "c": cleaned})
            continue
        out.append(block)
    return out


class SoftBreakRendering(StrEnum):
    SPACE = " "
    LINE = "\n"


class PandocBreakKind(StrEnum):
    SOFT = "soft"
    HARD = "hard"


@dataclass(frozen=True, slots=True)
class PandocTextAtom:
    value: str


type PandocInlineAtom = PandocTextAtom | PandocBreakKind


class PandocInlineError(ValueError):
    """A Pandoc inline cannot be projected without guessing its shape."""


def _inline_payload(value: object, *, path: str, kind: str, arity: int) -> list[object]:
    if not isinstance(value, list) or len(value) != arity:
        raise PandocInlineError(f"{path} {kind}: expected a {arity}-field payload")
    return cast("list[object]", value)


def _inline_atoms(inlines: object, *, path: str = "inlines") -> tuple[PandocInlineAtom, ...]:
    if not isinstance(inlines, list):
        raise PandocInlineError(f"{path}: expected an inline list")

    atoms: list[PandocInlineAtom] = []
    for index, item in enumerate(inlines):
        item_path = f"{path}[{index}]"
        if not isinstance(item, dict):
            raise PandocInlineError(f"{item_path}: expected an inline object")
        node = cast("dict[str, object]", item)
        kind = node.get("t")
        value = node.get("c")
        if not isinstance(kind, str):
            raise PandocInlineError(f"{item_path}: missing inline kind")

        match kind:
            case "Str":
                if not isinstance(value, str):
                    raise PandocInlineError(f"{item_path} Str: expected text payload")
                atoms.append(PandocTextAtom(value))
            case "Space":
                atoms.append(PandocTextAtom(" "))
            case "SoftBreak":
                atoms.append(PandocBreakKind.SOFT)
            case "LineBreak":
                atoms.append(PandocBreakKind.HARD)
            case (
                "Strong"
                | "Emph"
                | "Underline"
                | "SmallCaps"
                | "Strikeout"
                | "Superscript"
                | "Subscript"
            ):
                atoms.extend(_inline_atoms(value, path=f"{item_path}.{kind}"))
            case "Quoted" | "Cite" | "Span":
                payload = _inline_payload(value, path=item_path, kind=kind, arity=2)
                atoms.extend(_inline_atoms(payload[1], path=f"{item_path}.{kind}"))
            case "Code" | "Math":
                payload = _inline_payload(value, path=item_path, kind=kind, arity=2)
                text = payload[1]
                if not isinstance(text, str):
                    raise PandocInlineError(f"{item_path} {kind}: expected text at field 1")
                atoms.append(PandocTextAtom(text))
            case "Link":
                payload = _inline_payload(value, path=item_path, kind=kind, arity=3)
                atoms.extend(_inline_atoms(payload[1], path=f"{item_path}.{kind}"))
            case "Image" | "Note" | "RawInline":
                pass
            case str() as unknown:
                raise PandocInlineError(f"{item_path}: unsupported inline kind {unknown!r}")
    return tuple(atoms)


def inline_text(
    inlines: object,
    *,
    soft_break: SoftBreakRendering,
) -> str:
    """Project validated visible inlines without leaking opaque payload."""
    out: list[str] = []
    for atom in _inline_atoms(inlines):
        match atom:
            case PandocTextAtom(value=value):
                out.append(value)
            case PandocBreakKind.SOFT:
                out.append(soft_break.value)
            case PandocBreakKind.HARD:
                out.append("\n")
            case _:
                assert_never(atom)
    return "".join(out)


def inline_break_kinds(inlines: object) -> frozenset[PandocBreakKind]:
    """Break constructors present in the validated visible inline stream."""
    return frozenset(atom for atom in _inline_atoms(inlines) if isinstance(atom, PandocBreakKind))


_PANDOC_PREFIXES = frozenset({"w", "wp", "a", "pic", "r"})
_PANDOC_NAMESPACE_BINDINGS = tuple(
    binding
    for binding in ooxml.COMMON_NAMESPACES
    if binding.prefix in _PANDOC_PREFIXES
)
_CANONICAL_NS = {
    binding.uri: binding.prefix for binding in _PANDOC_NAMESPACE_BINDINGS
}
_PANDOC_STORY_PARTS = tuple(part.value for part in docx_source.StoryPart)


def _needs_canonicalization(bindings: Sequence[ooxml.NamespaceBinding]) -> bool:
    return any(
        binding.uri in _CANONICAL_NS
        and binding.prefix != _CANONICAL_NS[binding.uri]
        for binding in bindings
    )


def _remove_pagination_only_paragraphs(
    root: ET.Element,
    elements: Sequence[ET.Element],
    dispositions: Sequence[docx_source.ParagraphDisposition],
) -> bool:
    parents = {child: parent for parent in root.iter() for child in parent}
    if len(elements) != len(dispositions):
        raise RuntimeError(
            "source/projector paragraph mismatch "
            f"({len(dispositions)} != {len(elements)})"
        )
    changed = False
    for element, disposition in zip(elements, dispositions, strict=True):
        if disposition is not docx_source.ParagraphDisposition.PAGINATION_ONLY:
            continue
        parent = parents.get(element)
        if parent is None:
            raise RuntimeError("pagination-only paragraph has no parent")
        parent.remove(element)
        changed = True
    return changed


def _materialize_baseline_content(root: ET.Element) -> bool:
    """Mutate a target tree to the source-owned baseline child selection."""
    changed = False

    def project(parent: ET.Element) -> None:
        nonlocal changed
        original = tuple(parent)
        selected = tuple(docx_source.iter_baseline_children(parent))
        if selected != original:
            changed = True
        for child in selected:
            project(child)
        parent[:] = selected

    project(root)
    return changed


def _paragraph_location(root: ET.Element, element: ET.Element) -> str:
    parents = {child: parent for parent in root.iter() for child in parent}
    paragraph_indexes = {
        paragraph: index for index, paragraph in enumerate(root.iter(f"{W}p"))
    }
    owner = element
    while owner not in paragraph_indexes and owner in parents:
        owner = parents[owner]
    index = paragraph_indexes.get(owner)
    return f"paragraph {index}" if index is not None else "outside a paragraph"


def _story_evidence(
    part: str,
    root: ET.Element,
) -> tuple[tuple[ET.Element, ...], tuple[ET.Element, ...]]:
    """Collect selected paragraphs and pagination controls in one pass."""
    paragraphs: list[ET.Element] = []
    pagination: list[ET.Element] = []
    try:
        for element in docx_source.iter_baseline_descendants(root):
            if element.tag == f"{W}p":
                paragraphs.append(element)
                continue
            if element.tag != f"{W}br":
                continue
            try:
                kind = docx_source.BreakKind.from_ooxml(element.get(f"{W}type"))
            except docx_source.DocxSourceError as exc:
                raise docx_source.DocxSourceError(
                    f"{part}: {_paragraph_location(root, element)}: {exc}"
                ) from exc
            if kind.is_pagination:
                pagination.append(element)
    except docx_source.AlternateContentError as exc:
        raise docx_source.DocxSourceError(
            f"{part}: {_paragraph_location(root, exc.element)}: {exc}"
        ) from exc
    return tuple(paragraphs), tuple(pagination)


def _inject_source_anchors(
    body: ET.Element,
    elements: Sequence[ET.Element],
    paragraphs: Sequence[docx_source.SourceParagraph],
) -> bool:
    """Bookmark every content `w:p` with its ordinal and append the link farm.

    Only `anchored_ordinals` get bookmarks. The farm paragraphs exist solely to
    defeat Pandoc's orphan-anchor pruning and are dropped by the adapter."""
    existing_ids: list[int] = []
    for existing in body.iter(f"{W}bookmarkStart"):
        if (existing.get(f"{W}name") or "").startswith(SOURCE_ANCHOR_PREFIX):
            raise RuntimeError(
                f"source document already contains a {SOURCE_ANCHOR_PREFIX}* bookmark "
                f"({existing.get(f'{W}name')!r}) — the anchor namespace is reserved"
            )
        raw_id = existing.get(f"{W}id") or ""
        if raw_id.lstrip("-").isdigit():
            existing_ids.append(int(raw_id))
    next_id = max([_ANCHOR_BOOKMARK_ID_BASE, *(i + 1 for i in existing_ids)])

    def allocate_id() -> str:
        nonlocal next_id
        value = str(next_id)
        next_id += 1
        return value

    names: list[str] = []
    for element, paragraph in zip(elements, paragraphs, strict=True):
        if paragraph.disposition is not docx_source.ParagraphDisposition.CONTENT:
            continue
        ordinal = int(paragraph.ordinal)
        name = source_anchor_name(ordinal)
        bookmark_id = allocate_id()
        # Trailing placement is load-bearing: Pandoc's docx reader drops a
        # LEADING bookmark from the paragraph after an empty one.
        element.append(
            ET.Element(f"{W}bookmarkStart", {f"{W}id": bookmark_id, f"{W}name": name})
        )
        element.append(ET.Element(f"{W}bookmarkEnd", {f"{W}id": bookmark_id}))
        names.append(name)
    if not names:
        return False

    farm_at = len(body)
    if len(body) and body[-1].tag == f"{W}sectPr":
        farm_at -= 1
    for chunk, offset in enumerate(range(0, len(names), _ANCHOR_FARM_CHUNK)):
        marker = f"{_FARM_MARKER_PREFIX}{chunk}"
        marker_id = allocate_id()
        farm = ET.Element(f"{W}p")
        for name in (marker, *names[offset:offset + _ANCHOR_FARM_CHUNK]):
            link = ET.SubElement(farm, f"{W}hyperlink", {f"{W}anchor": name})
            run = ET.SubElement(link, f"{W}r")
            text = ET.SubElement(run, f"{W}t")
            text.text = "."
        farm.append(
            ET.Element(f"{W}bookmarkStart", {f"{W}id": marker_id, f"{W}name": marker})
        )
        farm.append(ET.Element(f"{W}bookmarkEnd", {f"{W}id": marker_id}))
        body.insert(farm_at, farm)
        farm_at += 1
    return True


def _project_part(
    part: str,
    xml: bytes,
    source: docx_source.DocxSourceDocument | None = None,
    *,
    styles: docx_source.ParagraphStyles,
) -> bytes:
    """Project one Word story part without teaching the domain about note internals."""
    try:
        parsed = ooxml.parse_xml(xml)
    except ET.ParseError as exc:
        raise RuntimeError(f"{part}: cannot parse OOXML for Pandoc projection") from exc
    root = parsed.root
    namespace_repair = _needs_canonicalization(
        parsed.bindings
    ) or any(reference.uri is None for reference in parsed.references_in())
    story_paragraphs, pagination_breaks = _story_evidence(part, root)
    if source is not None:
        body = root.find(f"{W}body")
        elements = docx_source.body_paragraph_elements(body) if body is not None else ()
        if len(elements) != len(source.paragraphs):
            raise RuntimeError(
                f"{source.path.name}: source/projector paragraph mismatch "
                f"({len(source.paragraphs)} != {len(elements)})"
            )
        dispositions = tuple(paragraph.disposition for paragraph in source.paragraphs)
    else:
        elements = story_paragraphs
        dispositions = tuple(
            docx_source.analyze_paragraph(element, styles=styles).disposition
            for element in elements
        )
    lowered_alternatives = _materialize_baseline_content(root)
    removed = _remove_pagination_only_paragraphs(root, elements, dispositions)
    injected = False
    if source is not None:
        body = root.find(f"{W}body")
        if body is not None:
            injected = _inject_source_anchors(body, elements, source.paragraphs)
    if not (pagination_breaks or removed or lowered_alternatives or namespace_repair or injected):
        return xml

    for element in pagination_breaks:
        element.tag = f"{W}t"
        element.attrib.clear()
        element.set(ooxml.XML_SPACE, "preserve")
        element.text = " "

    try:
        return ooxml.serialize_xml(
            parsed,
            bindings=_PANDOC_NAMESPACE_BINDINGS,
        )
    except ooxml.OoxmlNamespaceError as exc:
        raise RuntimeError(f"{part}: cannot close OOXML namespaces") from exc


def project_package(source: docx_source.DocxSourceDocument, work_dir: Path) -> Path:
    """Build the namespace-safe, pagination-free scratch package Pandoc consumes."""
    try:
        with zipfile.ZipFile(source.path) as src:
            entries = src.infolist()
            duplicate_story_parts = sorted(
                part
                for part in _PANDOC_STORY_PARTS
                if sum(info.filename == part for info in entries) > 1
            )
            if duplicate_story_parts:
                raise RuntimeError(
                    f"{source.path.name}: duplicate Word story part(s): "
                    + ", ".join(duplicate_story_parts)
                )
            replacements: dict[int, bytes] = {}
            for part in _PANDOC_STORY_PARTS:
                try:
                    info = src.getinfo(part)
                except KeyError:
                    continue
                original = src.read(info)
                projected = _project_part(
                    part,
                    original,
                    source if part == docx_source.DOCUMENT_PART else None,
                    styles=source.styles,
                )
                if projected != original:
                    replacements[entries.index(info)] = projected
            if not replacements:
                return source.path

            out_dir = work_dir / "_pandoc_docx"
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_dir / f"{source.path.stem}.docx"
            with zipfile.ZipFile(out, "w") as dst:
                dst.comment = src.comment
                for index, info in enumerate(entries):
                    dst.writestr(info, replacements.get(index, src.read(info)))
    except (OSError, zipfile.BadZipFile) as exc:
        raise RuntimeError(
            f"{source.path.name}: cannot project DOCX package for Pandoc"
        ) from exc
    return out


def run_json(
    source: docx_source.DocxSourceDocument,
    media_dir: Path,
) -> tuple[dict[str, Any], str]:
    """Project one source aggregate, then parse it to Pandoc JSON."""
    pandoc_docx = project_package(source, media_dir)
    cmd = [
        pandoc_argv0(), "--from", "docx+empty_paragraphs", "--to", "json",
        "--extract-media", str(media_dir), str(pandoc_docx),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=PANDOC_TIMEOUT_SECONDS, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"pandoc timed out after {PANDOC_TIMEOUT_SECONDS}s on {source.path.name}; "
            "the conversion was aborted (no partial output is trusted)."
        ) from exc
    if proc.returncode != 0:
        raise RuntimeError(f"pandoc failed on {source.path.name}: {proc.stderr.strip()}")
    return json.loads(proc.stdout), proc.stderr.strip()
