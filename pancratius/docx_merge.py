"""Merge multipart source DOCX files into one package-validated source document."""

from __future__ import annotations

import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path

from pancratius.docx_outline import OutlineSummary, PartBoundary, apply_part_outline
from pancratius.ooxml import (
    EMBED_REL_TYPES,
    R_NS,
    OoxmlRelationship,
    office_relationship_refs,
    read_ooxml_relationships,
    relationships_part_for,
)

OFFICE_DOCUMENT_REL = f"{R_NS}/officeDocument"


class DocxMergeError(ValueError):
    """The requested DOCX merge cannot be completed safely."""


class DocxMergeUsageError(DocxMergeError):
    """The requested merge has invalid user input."""


class DocxMergeOperationError(DocxMergeError):
    """The merge operation produced or encountered an invalid result."""


@dataclass(frozen=True)
class DocxValidationSummary:
    path: Path
    package_parts: int
    xml_parts: int
    relationships: int
    relationship_refs: int
    media_parts: int


@dataclass(frozen=True)
class MergeSummary:
    inputs: tuple[Path, ...]
    output: Path
    validation: DocxValidationSummary
    outline: OutlineSummary | None


def _validate_root_office_document(
    path: Path,
    relationships_by_part: dict[str, dict[str, OoxmlRelationship]],
) -> None:
    relationships = relationships_by_part.get("_rels/.rels", {})
    office_relationships = [
        relationship
        for relationship in relationships.values()
        if relationship.rel_type == OFFICE_DOCUMENT_REL
    ]
    if len(office_relationships) != 1:
        raise DocxMergeOperationError(
            f"{path}:root officeDocument relationship must point to word/document.xml"
        )
    relationship = office_relationships[0]
    if relationship.target_mode == "External":
        raise DocxMergeOperationError(f"{path}:root officeDocument relationship is external")
    if relationship.resolved_target != "word/document.xml":
        raise DocxMergeOperationError(
            f"{path}:root officeDocument relationship must point to word/document.xml "
            f"(got {relationship.resolved_target or '<missing>'})"
        )


def validate_docx_package(path: Path) -> DocxValidationSummary:
    """Validate ZIP/XML integrity and internal relationship references."""
    try:
        with zipfile.ZipFile(path) as zf:
            bad_member = zf.testzip()
            if bad_member is not None:
                raise DocxMergeOperationError(f"{path} has a corrupt ZIP member: {bad_member}")
            names = set(zf.namelist())
            payload = {name: zf.read(name) for name in names}
    except zipfile.BadZipFile as exc:
        raise DocxMergeOperationError(f"{path} is not a valid ZIP/DOCX package") from exc

    for required in ("[Content_Types].xml", "_rels/.rels", "word/document.xml"):
        if required not in names:
            raise DocxMergeOperationError(f"{path} is missing required DOCX part: {required}")

    xml_names = sorted(name for name in names if name.endswith((".xml", ".rels")))
    for name in xml_names:
        try:
            ET.fromstring(payload[name])
        except ET.ParseError as exc:
            raise DocxMergeOperationError(f"{path}:{name} is not well-formed XML: {exc}") from exc

    relationship_count = 0
    relationships_by_part: dict[str, dict[str, OoxmlRelationship]] = {}
    for rels_name in sorted(name for name in names if name.endswith(".rels")):
        root = ET.fromstring(payload[rels_name])
        read = read_ooxml_relationships(root, rels_name, names)
        if read.issues:
            raise DocxMergeOperationError(f"{path}:{read.issues[0]}")
        relationship_count += len(read.relationships)
        relationships_by_part[rels_name] = read.relationships
    _validate_root_office_document(path, relationships_by_part)

    relationship_refs = 0
    for name in sorted(n for n in names if n.endswith(".xml") and not n.endswith(".rels")):
        root = ET.fromstring(payload[name])
        refs = office_relationship_refs(root)
        relationship_refs += len(refs)
        if not refs:
            continue
        rels_name = relationships_part_for(name)
        relationships = relationships_by_part.get(rels_name)
        if relationships is None:
            raise DocxMergeOperationError(
                f"{path}:{name} uses relationships but {rels_name} is missing"
            )
        missing = sorted({ref.rel_id for ref in refs if ref.rel_id not in relationships})
        if missing:
            raise DocxMergeOperationError(
                f"{path}:{name} has unresolved relationship reference(s): {', '.join(missing)}"
            )
        for ref in refs:
            relationship = relationships[ref.rel_id]
            if ref.attr_name != "embed":
                continue
            if relationship.target_mode == "External":
                raise DocxMergeOperationError(
                    f"{path}:{name} has r:embed={relationship.rel_id} pointing to "
                    "an external relationship"
                )
            if relationship.rel_type not in EMBED_REL_TYPES:
                raise DocxMergeOperationError(
                    f"{path}:{name} has r:embed={relationship.rel_id} pointing to "
                    f"non-embeddable relationship type {relationship.rel_type}"
                )

    return DocxValidationSummary(
        path=path,
        package_parts=len(names),
        xml_parts=len(xml_names),
        relationships=relationship_count,
        relationship_refs=relationship_refs,
        media_parts=sum(1 for name in names if name.startswith("word/media/")),
    )


def _validate_inputs(inputs: tuple[Path, ...], output: Path) -> None:
    if not inputs:
        raise DocxMergeUsageError("at least one input DOCX is required")
    if output.suffix.lower() != ".docx":
        raise DocxMergeUsageError(f"expected a .docx output path, got {output}")
    if not output.parent.is_dir():
        raise DocxMergeUsageError(f"output parent does not exist: {output.parent}")
    output_resolved = output.resolve()
    seen_inputs: set[Path] = set()
    for src in inputs:
        if src.suffix.lower() != ".docx":
            raise DocxMergeUsageError(f"expected a .docx input, got {src}")
        if not src.is_file():
            raise DocxMergeUsageError(f"DOCX not found: {src}")
        src_resolved = src.resolve()
        if src_resolved in seen_inputs:
            raise DocxMergeUsageError(f"duplicate input DOCX: {src}")
        seen_inputs.add(src_resolved)
        if src_resolved == output_resolved:
            raise DocxMergeUsageError("output path must be different from every input path")


def _validate_parts(inputs: tuple[Path, ...], parts: tuple[PartBoundary, ...]) -> None:
    if not parts:
        return
    if len(inputs) < 2:
        raise DocxMergeUsageError("--part is only valid when merging multiple source DOCX files")
    if len(parts) != len(inputs):
        raise DocxMergeUsageError("--part must be repeated once per input DOCX")


def _compose(inputs: tuple[Path, ...], output: Path) -> None:
    try:
        from docx import Document
        from docxcompose.composer import Composer
    except ImportError as exc:
        raise DocxMergeOperationError(
            "docxcompose/python-docx is not available; run `uv sync` before merging DOCX files"
        ) from exc

    master = Document(str(inputs[0]))
    composer = Composer(master)
    for src in inputs[1:]:
        composer.append(Document(str(src)))
    composer.save(str(output))


def merge_docx(
    inputs: tuple[Path, ...],
    output: Path,
    *,
    parts: tuple[PartBoundary, ...] = (),
) -> MergeSummary:
    """Merge one or more DOCX files through the compose/package-validation pipeline."""
    normalized_inputs = tuple(src.expanduser().resolve() for src in inputs)
    normalized_output = output.expanduser().resolve()
    _validate_inputs(normalized_inputs, normalized_output)
    _validate_parts(normalized_inputs, parts)
    for src in normalized_inputs:
        try:
            validate_docx_package(src)
        except DocxMergeOperationError as exc:
            raise DocxMergeUsageError(f"invalid input DOCX {src}: {exc}") from exc

    with tempfile.TemporaryDirectory(prefix="docx-merge-", dir=normalized_output.parent) as td:
        tmp_dir = Path(td)
        raw = tmp_dir / "merged.docx"
        _compose(normalized_inputs, raw)
        candidate = raw
        outline_summary: OutlineSummary | None = None
        if parts:
            outlined = tmp_dir / "merged-outline.docx"
            outline_summary = apply_part_outline(raw, outlined, parts)
            candidate = outlined
        try:
            validation = validate_docx_package(candidate)
        except DocxMergeOperationError as exc:
            raise DocxMergeOperationError(f"merged DOCX failed package validation: {exc}") from exc
        shutil.copyfile(candidate, normalized_output)

    return MergeSummary(
        inputs=normalized_inputs,
        output=normalized_output,
        validation=DocxValidationSummary(
            path=normalized_output,
            package_parts=validation.package_parts,
            xml_parts=validation.xml_parts,
            relationships=validation.relationships,
            relationship_refs=validation.relationship_refs,
            media_parts=validation.media_parts,
        ),
        outline=outline_summary,
    )
