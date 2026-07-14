from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path

from pancratius.docx_source import (
    DocxSourceError,
    story_paragraph_elements,
)
from pancratius.ooxml import W
from pancratius.translation.docx.models import DocxTranslationError, WordTextSlot


def word_text_slots(document_root: ET.Element) -> tuple[WordTextSlot, ...]:
    slots: list[WordTextSlot] = []
    body = document_root.find(f"{W}body")
    if body is None:
        return ()
    try:
        paragraphs = story_paragraph_elements(body)
    except DocxSourceError as exc:
        raise DocxTranslationError(
            f"source DOCX story has unsupported content: {exc}"
        ) from exc
    for story_index, p in enumerate(paragraphs):
        try:
            slot = WordTextSlot(story_index=story_index, paragraph=p)
        except DocxSourceError as exc:
            raise DocxTranslationError(
                f"source DOCX paragraph {story_index} has unsupported content: {exc}"
            ) from exc
        slots.append(slot)
    return tuple(slots)


@dataclass(frozen=True, slots=True)
class DocxPackageParts:
    """A DOCX package payload plus the donor member order."""

    parts: dict[str, bytes]
    member_order: tuple[str, ...]


def copy_docx_parts(source_docx: Path) -> DocxPackageParts:
    try:
        with zipfile.ZipFile(source_docx) as zf:
            bad_member = zf.testzip()
            if bad_member is not None:
                raise DocxTranslationError(f"{source_docx} has a corrupt ZIP member: {bad_member}")
            member_order = tuple(zf.namelist())
            return DocxPackageParts(
                parts={name: zf.read(name) for name in member_order},
                member_order=member_order,
            )
    except (OSError, zipfile.BadZipFile) as exc:
        raise DocxTranslationError(f"{source_docx} is not a valid DOCX package") from exc
