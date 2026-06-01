"""Merge multipart source DOCX files into one package-validated source document."""

from __future__ import annotations

import posixpath
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from pancratius.docx_outline import OutlineSummary, PartBoundary, apply_part_outline

REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


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


def _rels_source_part(rels_name: str) -> str:
    if rels_name == "_rels/.rels":
        return ""
    if "/_rels/" not in rels_name or not rels_name.endswith(".rels"):
        raise DocxMergeOperationError(f"unexpected relationships part path: {rels_name}")
    prefix, leaf = rels_name.split("/_rels/", 1)
    return f"{prefix}/{leaf.removesuffix('.rels')}"


def _rels_part_for(source_part: str) -> str:
    if "/" in source_part:
        prefix, leaf = source_part.rsplit("/", 1)
        return f"{prefix}/_rels/{leaf}.rels"
    return f"_rels/{source_part}.rels"


def _resolve_relationship_target(source_part: str, target: str) -> str:
    parsed = urlsplit(target)
    path = unquote(parsed.path)
    if not path:
        raise DocxMergeOperationError(f"relationship target from {source_part or '/'} is empty")
    if path.startswith("/"):
        resolved = posixpath.normpath(path.lstrip("/"))
    else:
        base = posixpath.dirname(source_part)
        resolved = posixpath.normpath(posixpath.join(base, path))
    if resolved == "." or resolved.startswith("../"):
        raise DocxMergeOperationError(
            f"relationship target from {source_part or '/'} escapes the DOCX package: {target}"
        )
    return resolved


def _relationship_ids(rels_xml: bytes) -> set[str]:
    root = ET.fromstring(rels_xml)
    return {
        rel_id for rel in root.findall(f"{{{REL_NS}}}Relationship")
        if (rel_id := rel.get("Id"))
    }


def _relationship_refs(part_xml: bytes) -> set[str]:
    refs: set[str] = set()
    root = ET.fromstring(part_xml)
    for el in root.iter():
        for attr, value in el.attrib.items():
            if attr in {
                f"{{{OFFICE_REL_NS}}}id",
                f"{{{OFFICE_REL_NS}}}embed",
                f"{{{OFFICE_REL_NS}}}link",
            }:
                refs.add(value)
    return refs


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
    for rels_name in sorted(name for name in names if name.endswith(".rels")):
        source_part = _rels_source_part(rels_name)
        root = ET.fromstring(payload[rels_name])
        for rel in root.findall(f"{{{REL_NS}}}Relationship"):
            relationship_count += 1
            if rel.get("TargetMode") == "External":
                continue
            target = rel.get("Target", "")
            resolved = _resolve_relationship_target(source_part, target)
            if resolved not in names:
                rel_id = rel.get("Id", "<missing id>")
                raise DocxMergeOperationError(
                    f"{path}:{rels_name} relationship {rel_id} targets missing part {target!r}"
                )

    relationship_refs = 0
    for name in sorted(n for n in names if n.endswith(".xml") and not n.endswith(".rels")):
        refs = _relationship_refs(payload[name])
        relationship_refs += len(refs)
        if not refs:
            continue
        rels_name = _rels_part_for(name)
        if rels_name not in names:
            raise DocxMergeOperationError(
                f"{path}:{name} uses relationships but {rels_name} is missing"
            )
        ids = _relationship_ids(payload[rels_name])
        missing = sorted(ref for ref in refs if ref not in ids)
        if missing:
            raise DocxMergeOperationError(
                f"{path}:{name} has unresolved relationship reference(s): {', '.join(missing)}"
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
