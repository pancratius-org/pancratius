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

import hashlib
import json
import unicodedata
from pathlib import Path

from pancratius.ir import LineationRegister

_SHA_HEX = 16


def overrides_path(docx: Path) -> Path:
    """`<book>/<lang>.docx` → `<book>/lineation.<lang>.json`."""
    return docx.with_name(f"lineation.{docx.stem}.json")


def paragraph_sha(text: str) -> str:
    """The sidecar's text rail: sha256 of the NFC paragraph text, 16 hex. NFC so a cosmetic
    unicode re-encoding does not spuriously fail the rail, while any real edit does."""
    return hashlib.sha256(unicodedata.normalize("NFC", text).encode("utf-8")).hexdigest()[:_SHA_HEX]


def load_overrides(docx: Path) -> dict[int, LineationRegister]:
    """The validated corrections for one source DOCX (empty when no sidecar). FAILS LOUD on an
    unknown register, an ordinal with no source paragraph, or a text-rail mismatch."""
    path = overrides_path(docx)
    if not path.is_file():
        return {}
    from pancratius.docx_inspect import read_rows

    rows = {r.index: r for r in read_rows(docx)}
    out: dict[int, LineationRegister] = {}
    for key, entry in json.loads(path.read_text(encoding="utf-8")).items():
        ordinal = int(key)
        register, text_sha = entry["register"], entry["text_sha"]
        if register not in ("prose", "lineated"):
            raise ValueError(f"{path.name}: ordinal {ordinal} has register {register!r} "
                             f"(must be prose|lineated)")
        row = rows.get(ordinal)
        if row is None:
            raise ValueError(f"{path.name}: ordinal {ordinal} has no source paragraph in "
                             f"{docx.name} — the correction is stale; re-adjudicate or remove it")
        if paragraph_sha(row.text) != text_sha:
            raise ValueError(f"{path.name}: ordinal {ordinal} text drifted under the correction "
                             f"(rail {text_sha} != live {paragraph_sha(row.text)}) — "
                             f"re-adjudicate against the current text")
        out[ordinal] = register
    return out
