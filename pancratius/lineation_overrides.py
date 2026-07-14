"""Editorial lineation corrections — the committed per-book sidecar the importer honors.

`lineation.<lang>.json`, sibling of `<lang>.docx`, pins a human-adjudicated register for
specific source paragraphs the importer's own lineation ladder gets wrong:

    {"140": {"register": "prose", "text_sha": "0123456789abcdef"}}

Keys are source `w:p` ordinals; `text_sha` is `paragraph_sha` of the paragraph text the
correction was adjudicated against. The hash is a rail, never advisory: a mismatch means the
DOCX changed under the correction, and the load FAILS rather than apply (or silently skip) a
stale verdict. A missing sidecar means no corrections.

The adjudicated truth lives in the research label store; this sidecar is its committed
projection into production content (labels and sidecar move together, like docx and md).
"""
from __future__ import annotations

from pathlib import Path

from pancratius import docx_source
from pancratius.ir import LineationRegister

paragraph_sha = docx_source.paragraph_sha


def overrides_path(docx: Path) -> Path:
    """`<book>/<lang>.docx` → `<book>/lineation.<lang>.json`."""
    return docx.with_name(f"lineation.{docx.stem}.json")


def load_overrides(
    source: docx_source.DocxSourceDocument,
) -> dict[int, LineationRegister]:
    """The validated corrections for one source DOCX (empty when no sidecar). FAILS LOUD on a
    malformed sidecar, a non-canonical or duplicate ordinal key, an unknown register, an ordinal
    with no source paragraph, or a text-rail mismatch."""
    path = overrides_path(source.path)
    out: dict[int, LineationRegister] = {}
    for adjudication in docx_source.read_adjudications(source, path):
        ordinal = int(adjudication.paragraph.ordinal)
        if adjudication.paragraph.disposition is not docx_source.ParagraphDisposition.CONTENT:
            raise ValueError(
                f"{path.name}: ordinal {ordinal} is "
                f"{adjudication.paragraph.disposition.value}, not readable content — "
                "the adjudication is stale; re-adjudicate or remove it"
            )
        raw_register = adjudication.payload.get("register")
        if raw_register == "prose":
            register: LineationRegister = "prose"
        elif raw_register == "lineated":
            register = "lineated"
        else:
            raise ValueError(f"{path.name}: ordinal {ordinal} has register {raw_register!r} "
                             f"(must be prose|lineated)")
        out[ordinal] = register
    return out
