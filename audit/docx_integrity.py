"""Fast source-DOCX integrity audit.

Checks corpus DOCX files without launching Office:

* ZIP/XML/relationship package validity;
* exact duplicated body-text halves (the book-46 failure mode);
* duplicate media payloads inside one DOCX package.
"""
from __future__ import annotations

import hashlib
import os
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from pancratius.ooxml import (
    EMBED_REL_TYPES,
    R_NS,
    REL_NS,
    W_NS,
    OoxmlRelationship,
    OoxmlRelationshipError,
    office_relationship_refs,
    read_ooxml_relationships,
    relationships_part_for,
    resolve_relationship_target,
)

ROOT = Path(os.environ.get("PANCRATIUS_AUDIT_ROOT", Path(__file__).resolve().parents[1]))
CONTENT = ROOT / "src" / "content"
CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
OFFICE_DOCUMENT_REL = f"{R_NS}/officeDocument"
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
DUPLICATE_HALF_MIN_PARAGRAPHS = 6


class DocxIntegrityError(ValueError):
    """The DOCX package is not internally coherent enough to trust."""


def _docx_paths() -> list[Path]:
    return sorted(
        path
        for collection in (CONTENT / "books", CONTENT / "poetry")
        if collection.exists()
        for path in collection.glob("*/*.docx")
    )

def _parse_xml_part(zf: zipfile.ZipFile, name: str) -> ET.Element:
    try:
        return ET.fromstring(zf.read(name))
    except ET.ParseError as exc:
        raise DocxIntegrityError(f"{name} is not well-formed XML: {exc}") from exc

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
        try:
            targets.append(resolve_relationship_target("", rel.get("Target", "")))
        except OoxmlRelationshipError as exc:
            raise DocxIntegrityError(str(exc)) from exc
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
) -> dict[str, OoxmlRelationship]:
    root = _parse_xml_part(zf, rels_name)
    if rels_name == "_rels/.rels":
        _validate_root_office_document(root, names)
    read = read_ooxml_relationships(root, rels_name, names)
    if read.issues:
        raise DocxIntegrityError(read.issues[0])
    return read.relationships


def _validate_relationship_reference(
    part_name: str,
    attr_name: str,
    relationship: OoxmlRelationship,
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
    rels: dict[str, dict[str, OoxmlRelationship]],
) -> ET.Element:
    document_root: ET.Element | None = None
    for name in sorted(n for n in names if n.startswith("word/") and n.endswith(".xml")):
        root = _parse_xml_part(zf, name)
        if name == "word/document.xml":
            document_root = root
        refs = office_relationship_refs(root)
        if not refs:
            continue
        rels_name = relationships_part_for(name)
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
            rels: dict[str, dict[str, OoxmlRelationship]] = {}
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
