"""Pandoc anti-corruption layer for DOCX packages.

The source aggregate owns authored semantics. This adapter projects only the
Word story parts Pandoc reads into its poorer vocabulary, then invokes Pandoc.
"""

from __future__ import annotations

import json
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, assert_never, cast

from pancratius import docx_source, ooxml
from pancratius.ooxml import W
from pancratius.pandoc import pandoc_argv0

PANDOC_TIMEOUT_SECONDS = 300


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
    if not (pagination_breaks or removed or lowered_alternatives or namespace_repair):
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
