"""Unmarked-canon scripture pins — the committed per-book sidecar the importer honors.

`scripture.<lang>.json`, sibling of `<lang>.docx`, pins source paragraphs whose text IS a
quotation of an external canonical source (Bible/Quran/Enoch/…) carrying NO structural
marker the rule channels can read — recognizable only by knowing the canonical texts:

    {"140": {"source": "Откр 3:11", "text_sha": "0123456789abcdef"}}

Keys are source `w:p` ordinals; `source` names the canonical provenance the pin was
adjudicated against (audit trail, not consumed by the pass); `text_sha` is
`paragraph_sha` of the paragraph text. The hash is a rail, never advisory: a mismatch
means the DOCX changed under the pin, and the load FAILS rather than apply (or silently
skip) a stale verdict. A missing sidecar means no pins.

The adjudicated truth lives in the research label store (teacher consensus with
source-name agreement); this sidecar is its committed projection into production
content (labels and sidecar move together, like docx and md).
"""
from __future__ import annotations

from pathlib import Path

from pancratius import docx_source


def overrides_path(docx: Path) -> Path:
    """`<book>/<lang>.docx` → `<book>/scripture.<lang>.json`."""
    return docx.with_name(f"scripture.{docx.stem}.json")


def load_overrides(source: docx_source.DocxSourceDocument) -> dict[int, str]:
    """The validated scripture pins for one source DOCX (empty when no sidecar),
    ordinal → named canonical source. FAILS LOUD on a malformed sidecar, a
    non-canonical or duplicate ordinal key, a missing/empty source name, an ordinal
    with no source paragraph, or a text-rail mismatch."""
    path = overrides_path(source.path)
    out: dict[int, str] = {}
    for adjudication in docx_source.read_adjudications(source, path):
        ordinal = int(adjudication.paragraph.ordinal)
        named_source = adjudication.payload.get("source")
        if not (isinstance(named_source, str) and named_source.strip()):
            raise ValueError(f"{path.name}: ordinal {ordinal} must name its canonical source")
        paragraph = adjudication.paragraph
        if not paragraph.text:
            raise ValueError(f"{path.name}: ordinal {ordinal} is a blank paragraph — "
                             f"a pin must land on quotation text")
        out[ordinal] = named_source
    return out
