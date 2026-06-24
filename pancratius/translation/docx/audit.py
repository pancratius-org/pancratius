"""Committed translated-DOCX artifact checks for the transfer package."""

from __future__ import annotations

import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

WORK_COLLECTIONS = ("books", "poetry")
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"

type DocxPartName = str
type WordElementName = str
type WordFootnoteId = int


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
            messages = _footnote_issues(zf, names, document_root)
    except zipfile.BadZipFile:
        messages = ["not a valid ZIP/DOCX package"]
    except TranslatedDocxAuditError as exc:
        messages = [str(exc)]
    return [TranslatedDocxArtifactIssue(path, message) for message in messages]
