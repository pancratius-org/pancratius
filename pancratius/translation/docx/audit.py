"""Committed translated-DOCX artifact checks for the transfer package."""

from __future__ import annotations

import re
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from pancratius.ooxml import (
    DRAWING_METADATA_ATTRS,
    DRAWING_METADATA_ELEMENT_TAGS,
    DRAWING_METADATA_WORD_PART_RE,
    EMBED_REL_TYPES,
    OoxmlRelationship,
    W,
    local_name,
    office_relationship_refs,
    read_ooxml_relationships,
    relationships_part_for,
)

type DocxPartName = str
type OoxmlAttributeName = str
type WordElementName = str
type WordFootnoteId = int

WORK_COLLECTIONS = ("books", "poetry")
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")


@dataclass(frozen=True, slots=True)
class TranslatedDocxArtifactIssue:
    path: Path
    message: str


@dataclass(frozen=True, slots=True)
class TranslatedDocxArtifactAudit:
    checked_paths: tuple[Path, ...]
    issues: tuple[TranslatedDocxArtifactIssue, ...]

    @property
    def checked(self) -> int:
        return len(self.checked_paths)

    @property
    def failed(self) -> bool:
        return bool(self.issues)


class TranslatedDocxAuditError(ValueError):
    """The translated DOCX cannot be inspected."""


@dataclass(frozen=True, slots=True)
class FootnoteIdRead:
    positive_ids: frozenset[WordFootnoteId]
    positive_sequence: tuple[WordFootnoteId, ...]
    issues: tuple[str, ...]


def audit_translated_docx_artifacts(repo_root: Path) -> TranslatedDocxArtifactAudit:
    paths = _translated_docx_paths(repo_root)
    issues = tuple(issue for path in paths for issue in _docx_issues(path))
    return TranslatedDocxArtifactAudit(tuple(paths), issues)


def _translated_docx_paths(repo_root: Path) -> list[Path]:
    content = repo_root / "src" / "content"
    if not content.exists():
        return []
    return sorted(
        path
        for collection in WORK_COLLECTIONS
        for path in (content / collection).glob("*/*.docx")
        if path.name != "ru.docx"
    )


def _parse_xml(zf: zipfile.ZipFile, part_name: DocxPartName) -> ET.Element:
    try:
        return ET.fromstring(zf.read(part_name))
    except KeyError as exc:
        raise TranslatedDocxAuditError(f"missing required DOCX part: {part_name}") from exc
    except ET.ParseError as exc:
        raise TranslatedDocxAuditError(f"{part_name} is not well-formed XML: {exc}") from exc


def _drawing_metadata_issues(zf: zipfile.ZipFile, names: set[str]) -> list[str]:
    issues: list[str] = []
    for part_name in sorted(name for name in names if DRAWING_METADATA_WORD_PART_RE.fullmatch(name)):
        root = _parse_xml(zf, part_name)
        for element in root.iter():
            if element.tag not in DRAWING_METADATA_ELEMENT_TAGS:
                continue
            for attr_name in DRAWING_METADATA_ATTRS:
                value = element.get(attr_name)
                if value is not None and _CYRILLIC_RE.search(value):
                    issues.append(
                        _drawing_metadata_issue(part_name, local_name(element.tag), attr_name, value)
                    )
    return issues


def _drawing_metadata_issue(
    part_name: DocxPartName,
    element_name: WordElementName,
    attr_name: OoxmlAttributeName,
    value: str,
) -> str:
    preview = value if len(value) <= 80 else f"{value[:77]}..."
    return f"{part_name} {element_name}@{attr_name} contains Cyrillic text {preview!r}"


def _relationship_issues(zf: zipfile.ZipFile, names: set[str]) -> list[str]:
    issues: list[str] = []
    relationships_by_part: dict[DocxPartName, dict[str, OoxmlRelationship]] = {}
    relationship_media_parts: set[DocxPartName] = set()
    for rels_name in sorted(name for name in names if name == "_rels/.rels" or name.endswith(".rels")):
        root = _parse_xml(zf, rels_name)
        read = read_ooxml_relationships(root, rels_name, names)
        issues.extend(read.issues)
        relationships_by_part[rels_name] = read.relationships
        for relationship in read.relationships.values():
            if (
                relationship.resolved_target is not None
                and relationship.resolved_target.startswith("word/media/")
            ):
                relationship_media_parts.add(relationship.resolved_target)
    for part_name in sorted(name for name in names if name.startswith("word/") and name.endswith(".xml")):
        root = _parse_xml(zf, part_name)
        refs = office_relationship_refs(root)
        if not refs:
            continue
        rels_name = relationships_part_for(part_name)
        relationships = relationships_by_part.get(rels_name, {})
        missing = sorted(ref.rel_id for ref in refs if ref.rel_id not in relationships)
        if missing:
            issues.append(f"{part_name} has unresolved relationship reference(s): {', '.join(missing)}")
        for ref in refs:
            relationship = relationships.get(ref.rel_id)
            if relationship is None:
                continue
            if ref.attr_name == "embed" and relationship.target_mode == "External":
                issues.append(
                    f"{part_name} has r:embed={relationship.rel_id} pointing to an external relationship"
                )
            if ref.attr_name == "embed" and relationship.rel_type not in EMBED_REL_TYPES:
                issues.append(
                    f"{part_name} has r:embed={relationship.rel_id} pointing to "
                    f"non-embeddable relationship type {relationship.rel_type}"
                )
    for media_part in sorted(name for name in names if name.startswith("word/media/")):
        if media_part not in relationship_media_parts:
            issues.append(f"{media_part} has no internal package relationship")
    return issues


def _positive_footnote_ids(
    root: ET.Element,
    *,
    element_name: WordElementName,
    source: DocxPartName,
    allow_reserved_ids: bool,
) -> FootnoteIdRead:
    ids: list[WordFootnoteId] = []
    issues: list[str] = []
    for element in root.findall(f".//{W}{element_name}"):
        raw = element.get(f"{W}id")
        label = f"{source} {element_name}"
        if raw is None or raw == "":
            issues.append(f"{label} is missing w:id")
            continue
        try:
            numeric_id = int(raw)
        except ValueError:
            issues.append(f"{label} has non-integer w:id {raw!r}")
            continue
        if numeric_id <= 0:
            if not allow_reserved_ids:
                issues.append(f"{label} uses non-positive w:id {numeric_id}")
            continue
        ids.append(numeric_id)
    return FootnoteIdRead(frozenset(ids), tuple(ids), tuple(issues))


def _footnote_issues(zf: zipfile.ZipFile, names: set[str], document_root: ET.Element) -> list[str]:
    references = _positive_footnote_ids(
        document_root,
        element_name="footnoteReference",
        source="word/document.xml",
        allow_reserved_ids=False,
    )
    if "word/footnotes.xml" not in names:
        if not references.positive_ids:
            return list(references.issues)
        return [
            *references.issues,
            "word/document.xml has footnote references but word/footnotes.xml is missing",
        ]
    footnotes_root = _parse_xml(zf, "word/footnotes.xml")
    definitions = _positive_footnote_ids(
        footnotes_root,
        element_name="footnote",
        source="word/footnotes.xml",
        allow_reserved_ids=True,
    )
    issues = [*references.issues, *definitions.issues]
    duplicate_references = sorted(
        footnote_id
        for footnote_id, count in Counter(references.positive_sequence).items()
        if count > 1
    )
    if duplicate_references:
        issues.append(
            "word/document.xml has duplicate positive footnote reference ids "
            f"{duplicate_references!r}"
        )
    duplicate_definitions = sorted(
        footnote_id
        for footnote_id, count in Counter(definitions.positive_sequence).items()
        if count > 1
    )
    if duplicate_definitions:
        issues.append(
            "word/footnotes.xml has duplicate positive footnote definition ids "
            f"{duplicate_definitions!r}"
        )
    if references.positive_ids == definitions.positive_ids:
        return issues
    return [
        *issues,
        "body footnote reference ids "
        f"{sorted(references.positive_ids)!r} do not match positive footnote definition ids "
        f"{sorted(definitions.positive_ids)!r}",
    ]


def _docx_issues(path: Path) -> list[TranslatedDocxArtifactIssue]:
    try:
        with zipfile.ZipFile(path) as zf:
            bad_member = zf.testzip()
            if bad_member is not None:
                raise TranslatedDocxAuditError(f"corrupt ZIP member: {bad_member}")
            names = set(zf.namelist())
            document_root = _parse_xml(zf, "word/document.xml")
            messages = [
                *_relationship_issues(zf, names),
                *_footnote_issues(zf, names, document_root),
                *_drawing_metadata_issues(zf, names),
            ]
    except zipfile.BadZipFile:
        messages = ["not a valid ZIP/DOCX package"]
    except TranslatedDocxAuditError as exc:
        messages = [str(exc)]
    return [TranslatedDocxArtifactIssue(path, message) for message in messages]
