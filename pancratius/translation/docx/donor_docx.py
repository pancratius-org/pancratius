from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path

from pancratius.ooxml import W
from pancratius.translation.docx.models import DocxTranslationError, WordTextSlot


def word_paragraph_text(p: ET.Element) -> str:
    parts: list[str] = []
    for el in p.iter():
        if el.tag == f"{W}t":
            parts.append(el.text or "")
        elif el.tag in {f"{W}br", f"{W}cr"}:
            parts.append("\n")
        elif el.tag == f"{W}tab":
            parts.append("\t")
    return "".join(parts).strip()


def word_text_slots(document_root: ET.Element) -> tuple[WordTextSlot, ...]:
    slots: list[WordTextSlot] = []
    body = document_root.find(f"{W}body")
    if body is None:
        return ()
    for ordinal, p in enumerate(body.iter(f"{W}p")):
        slots.append(WordTextSlot(
            ordinal=ordinal,
            paragraph=p,
            text=word_paragraph_text(p),
            has_drawing=p.find(f".//{W}drawing") is not None or p.find(f".//{W}pict") is not None,
            footnote_refs=tuple(copy.deepcopy(r) for r in p.findall(f".//{W}footnoteReference/..")),
        ))
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
