# import-pure: no filesystem mutation
"""Read narrow DOCX paragraph-level signals that Pandoc's Markdown writer drops.

Pandoc is the content converter; this module only captures source signals that
are otherwise lost — paragraph alignment, bold/italic runs, and the Word paragraph
style. The IR adapter and the poem source-duplicate title strip consume these to
make decisions the Markdown alone cannot support.

PURE: this opens the DOCX with `zipfile` for READ only (`ZipFile`/`read`) and
parses `word/document.xml`. It mutates nothing on disk.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"


@dataclass(frozen=True)
class DocxParagraphMeta:
    text: str
    align: str
    style: str
    bold: bool
    italic: bool

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


def _w_val(el: ET.Element | None) -> str:
    if el is None:
        return ""
    return str(el.get(f"{W}val") or "")


def _run_prop_enabled(el: ET.Element | None) -> bool:
    if el is None:
        return False
    val = el.get(f"{W}val")
    return val not in {"0", "false", "False", "off"}


def read_docx_paragraph_meta(docx: Path) -> list[DocxParagraphMeta]:
    """Read paragraph-level Word metadata that Markdown cannot carry — alignment,
    style, bold/italic runs — in document order."""
    with zipfile.ZipFile(docx) as zf:
        root = ET.fromstring(zf.read("word/document.xml"))

    paras: list[DocxParagraphMeta] = []
    for p in root.iter(f"{W}p"):
        text_parts: list[str] = []
        for el in p.iter():
            if el.tag == f"{W}t":
                text_parts.append(el.text or "")
            elif el.tag in {f"{W}br", f"{W}cr"}:
                text_parts.append("\n")
            elif el.tag == f"{W}tab":
                text_parts.append("\t")

        ppr = p.find(f"{W}pPr")
        style = _w_val(ppr.find(f"{W}pStyle") if ppr is not None else None)
        align = _w_val(ppr.find(f"{W}jc") if ppr is not None else None)
        bold = any(_run_prop_enabled(el) for el in p.findall(f".//{W}b"))
        italic = any(_run_prop_enabled(el) for el in p.findall(f".//{W}i"))
        paras.append(DocxParagraphMeta(
            text="".join(text_parts).strip(),
            align=align,
            style=style,
            bold=bold,
            italic=italic,
        ))
    return paras
