"""Fast source-DOCX integrity audit.

Checks corpus DOCX files without launching Office:

* ZIP/XML/relationship package validity;
* exact duplicated body-text halves (the book-46 failure mode);
* duplicate media payloads inside one DOCX package.
"""
from __future__ import annotations

import hashlib
import os
import posixpath
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

ROOT = Path(os.environ.get("PANCRATIUS_AUDIT_ROOT", Path(__file__).resolve().parents[1]))
CONTENT = ROOT / "src" / "content"
CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
OFFICE_DOCUMENT_REL = f"{OFFICE_REL_NS}/officeDocument"
DOCUMENT_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
)
RELATIONSHIPS_CONTENT_TYPE = "application/vnd.openxmlformats-package.relationships+xml"
EXPECTED_CONTENT_TYPES = {
    "bmp": "image/bmp",
    "emf": "image/x-emf",
    "gif": "image/gif",
    "jpeg": "image/jpeg",
    "jpg": "image/jpeg",
    "png": "image/png",
    "rels": RELATIONSHIPS_CONTENT_TYPE,
    "svg": "image/svg+xml",
    "tif": "image/tiff",
    "tiff": "image/tiff",
    "wmf": "image/x-wmf",
}
EMBED_REL_TYPES = {
    f"{OFFICE_REL_NS}/audio",
    f"{OFFICE_REL_NS}/image",
    f"{OFFICE_REL_NS}/oleObject",
    f"{OFFICE_REL_NS}/package",
    f"{OFFICE_REL_NS}/video",
}
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
DUPLICATE_HALF_MIN_PARAGRAPHS = 6


class DocxIntegrityError(ValueError):
    """The DOCX package is not internally coherent enough to trust."""


@dataclass(frozen=True)
class Relationship:
    rel_id: str
    rel_type: str
    target: str
    target_mode: str | None
    resolved_target: str | None


@dataclass(frozen=True, slots=True)
class OfficeRelationshipRef:
    attr_name: str
    rel_id: str


def _docx_paths() -> list[Path]:
    return sorted(
        path
        for collection in (CONTENT / "books", CONTENT / "poetry")
        if collection.exists()
        for path in collection.glob("*/*.docx")
    )


def _rels_source_part(rels_name: str) -> str:
    if rels_name == "_rels/.rels":
        return ""
    if "/_rels/" not in rels_name or not rels_name.endswith(".rels"):
        raise DocxIntegrityError(f"unexpected relationships part path: {rels_name}")
    prefix, leaf = rels_name.split("/_rels/", 1)
    return f"{prefix}/{leaf.removesuffix('.rels')}"


def _resolve_relationship_target(source_part: str, target: str) -> str:
    parsed = urlsplit(target)
    path = unquote(parsed.path)
    if not path:
        raise DocxIntegrityError(f"relationship target from {source_part or '/'} is empty")
    if path.startswith("/"):
        resolved = posixpath.normpath(path.lstrip("/"))
    else:
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(source_part), path))
    if resolved == "." or resolved.startswith("../"):
        raise DocxIntegrityError(
            f"relationship target from {source_part or '/'} escapes the DOCX package: {target}"
        )
    return resolved


def _rels_part_for(source_part: str) -> str:
    if "/" in source_part:
        prefix, leaf = source_part.rsplit("/", 1)
        return f"{prefix}/_rels/{leaf}.rels"
    return f"_rels/{source_part}.rels"


def _parse_xml_part(zf: zipfile.ZipFile, name: str) -> ET.Element:
    try:
        return ET.fromstring(zf.read(name))
    except ET.ParseError as exc:
        raise DocxIntegrityError(f"{name} is not well-formed XML: {exc}") from exc


def _office_relationship_refs(root: ET.Element) -> list[OfficeRelationshipRef]:
    prefix = f"{{{OFFICE_REL_NS}}}"
    return [
        OfficeRelationshipRef(attr.removeprefix(prefix), value)
        for element in root.iter()
        for attr, value in element.attrib.items()
        if attr.startswith(prefix)
    ]


def _validate_content_types(root: ET.Element, names: set[str]) -> None:
    defaults = {
        element.get("Extension"): element.get("ContentType")
        for element in root.findall(f"{{{CONTENT_TYPES_NS}}}Default")
        if element.get("Extension") and element.get("ContentType")
    }
    overrides = {
        element.get("PartName", "").lstrip("/"): element.get("ContentType")
        for element in root.findall(f"{{{CONTENT_TYPES_NS}}}Override")
        if element.get("PartName")
    }
    document_content_type = overrides.get("word/document.xml") or defaults.get("xml")
    if document_content_type != DOCUMENT_CONTENT_TYPE:
        raise DocxIntegrityError(
            "word/document.xml has no main-document content type"
        )
    for name in sorted(n for n in names if not n.endswith("/") and n != "[Content_Types].xml"):
        extension = name.rsplit(".", 1)[1] if "." in name else ""
        content_type = overrides.get(name) or defaults.get(extension)
        if content_type is None:
            raise DocxIntegrityError(f"{name} has no content type declaration")
        if expected := EXPECTED_CONTENT_TYPES.get(extension):
            if content_type != expected:
                raise DocxIntegrityError(
                    f"{name} has content type {content_type!r}, expected {expected!r}"
                )
        elif name.startswith("word/media/"):
            raise DocxIntegrityError(f"{name} has unsupported media extension {extension!r}")


def _validate_root_office_document(
    root_relationships: ET.Element,
    names: set[str],
) -> None:
    targets: list[str] = []
    for rel in root_relationships.findall(f"{{{REL_NS}}}Relationship"):
        if rel.get("Type") != OFFICE_DOCUMENT_REL:
            continue
        if rel.get("TargetMode") == "External":
            raise DocxIntegrityError("root officeDocument relationship is external")
        targets.append(_resolve_relationship_target("", rel.get("Target", "")))
    if targets != ["word/document.xml"]:
        rendered = ", ".join(targets) if targets else "<missing>"
        raise DocxIntegrityError(
            "root officeDocument relationship must point to word/document.xml "
            f"(got {rendered})"
        )
    if "word/document.xml" not in names:
        raise DocxIntegrityError("missing required DOCX part: word/document.xml")


def _validate_relationship_part(
    zf: zipfile.ZipFile,
    names: set[str],
    rels_name: str,
) -> dict[str, Relationship]:
    root = _parse_xml_part(zf, rels_name)
    if rels_name == "_rels/.rels":
        _validate_root_office_document(root, names)
    source_part = _rels_source_part(rels_name)
    if source_part and source_part not in names:
        raise DocxIntegrityError(f"{rels_name} has no source part {source_part}")
    relationships: dict[str, Relationship] = {}
    for rel in root.findall(f"{{{REL_NS}}}Relationship"):
        rel_id = rel.get("Id")
        rel_type = rel.get("Type")
        if rel_id is None:
            raise DocxIntegrityError(f"{rels_name} has a relationship without Id")
        if rel_id in relationships:
            raise DocxIntegrityError(f"{rels_name} has duplicate relationship Id {rel_id}")
        if rel_type is None:
            raise DocxIntegrityError(f"{rels_name} relationship {rel_id} has no Type")
        target = rel.get("Target", "")
        if rel.get("TargetMode") == "External":
            relationships[rel_id] = Relationship(
                rel_id=rel_id,
                rel_type=rel_type,
                target=target,
                target_mode=rel.get("TargetMode"),
                resolved_target=None,
            )
            continue
        resolved = _resolve_relationship_target(source_part, target)
        if resolved not in names:
            raise DocxIntegrityError(
                f"{rels_name} relationship {rel_id} targets missing part {target!r}"
            )
        relationships[rel_id] = Relationship(
            rel_id=rel_id,
            rel_type=rel_type,
            target=target,
            target_mode=rel.get("TargetMode"),
            resolved_target=resolved,
        )
    return relationships


def _validate_relationship_reference(
    part_name: str,
    attr_name: str,
    relationship: Relationship,
) -> None:
    if attr_name == "embed" and relationship.target_mode == "External":
        raise DocxIntegrityError(
            f"{part_name} has r:embed={relationship.rel_id} pointing to an external relationship"
        )
    if attr_name == "embed" and relationship.rel_type not in EMBED_REL_TYPES:
        raise DocxIntegrityError(
            f"{part_name} has r:embed={relationship.rel_id} pointing to "
            f"non-embeddable relationship type {relationship.rel_type}"
        )


def _validate_xml_relationship_refs(
    zf: zipfile.ZipFile,
    names: set[str],
    rels: dict[str, dict[str, Relationship]],
) -> ET.Element:
    document_root: ET.Element | None = None
    for name in sorted(n for n in names if n.startswith("word/") and n.endswith(".xml")):
        root = _parse_xml_part(zf, name)
        if name == "word/document.xml":
            document_root = root
        refs = _office_relationship_refs(root)
        if not refs:
            continue
        rels_name = _rels_part_for(name)
        relationships = rels.get(rels_name, {})
        missing = sorted(ref.rel_id for ref in refs if ref.rel_id not in relationships)
        if missing:
            raise DocxIntegrityError(
                f"{name} has unresolved relationship reference(s): {', '.join(missing)}"
            )
        for ref in refs:
            _validate_relationship_reference(name, ref.attr_name, relationships[ref.rel_id])
    if document_root is None:
        raise DocxIntegrityError("missing required DOCX part: word/document.xml")
    return document_root


def _validate_docx_package(path: Path) -> tuple[set[str], ET.Element]:
    try:
        with zipfile.ZipFile(path) as zf:
            name_list = zf.namelist()
            names = set(name_list)
            if len(names) != len(name_list):
                duplicates = sorted({name for name in name_list if name_list.count(name) > 1})
                raise DocxIntegrityError(
                    f"duplicate ZIP part name(s): {', '.join(duplicates)}"
                )
            for required in ("[Content_Types].xml", "_rels/.rels", "word/document.xml"):
                if required not in names:
                    raise DocxIntegrityError(f"missing required DOCX part: {required}")
            rels: dict[str, dict[str, Relationship]] = {}
            for name in sorted(n for n in names if n.endswith(".rels")):
                rels[name] = _validate_relationship_part(zf, names, name)
            _validate_content_types(_parse_xml_part(zf, "[Content_Types].xml"), names)
            document_root = _validate_xml_relationship_refs(zf, names, rels)
            return names, document_root
    except zipfile.BadZipFile as exc:
        raise DocxIntegrityError("not a valid ZIP/DOCX package") from exc


def _paragraph_texts(root: ET.Element) -> list[str]:
    return [
        " ".join(t.text or "" for t in paragraph.findall(f".//{{{W_NS}}}t")).strip()
        for paragraph in root.findall(f".//{{{W_NS}}}p")
    ]


def _duplicate_text_half(path: Path, document_root: ET.Element) -> str | None:
    nonempty = [text for text in _paragraph_texts(document_root) if text]
    if len(nonempty) < DUPLICATE_HALF_MIN_PARAGRAPHS or len(nonempty) % 2 != 0:
        return None
    mid = len(nonempty) // 2
    if nonempty[:mid] != nonempty[mid:]:
        return None
    return f"{path}: body text appears duplicated exactly ({len(nonempty)} non-empty paragraphs)"


def _duplicate_media(path: Path, names: set[str]) -> str | None:
    media = [name for name in names if name.startswith("word/media/")]
    with zipfile.ZipFile(path) as zf:
        by_hash: dict[str, list[str]] = {}
        for name in media:
            digest = hashlib.sha256(zf.read(name)).hexdigest()
            by_hash.setdefault(digest, []).append(name)
    duplicates = [names for names in by_hash.values() if len(names) > 1]
    if not duplicates:
        return None
    rendered = "; ".join(", ".join(names) for names in duplicates)
    return f"{path}: duplicate media payload(s): {rendered}"


def main() -> int:
    failures: list[str] = []
    paths = _docx_paths()
    for path in paths:
        try:
            names, document_root = _validate_docx_package(path)
        except DocxIntegrityError as exc:
            failures.append(f"{path}: invalid DOCX package: {exc}")
            continue
        if (duplicate := _duplicate_text_half(path, document_root)) is not None:
            failures.append(duplicate)
        if (duplicate := _duplicate_media(path, names)) is not None:
            failures.append(duplicate)

    if failures:
        print(f"FAIL: {len(failures)} DOCX integrity issue(s)", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    print(f"checked {len(paths)} DOCX source file(s); package/text/media integrity clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
