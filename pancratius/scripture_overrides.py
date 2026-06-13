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

import json
from pathlib import Path

from pancratius.lineation_overrides import paragraph_sha


def overrides_path(docx: Path) -> Path:
    """`<book>/<lang>.docx` → `<book>/scripture.<lang>.json`."""
    return docx.with_name(f"scripture.{docx.stem}.json")


def load_overrides(docx: Path) -> dict[int, str]:
    """The validated scripture pins for one source DOCX (empty when no sidecar),
    ordinal → named canonical source. FAILS LOUD on a malformed sidecar, a
    non-canonical or duplicate ordinal key, a missing/empty source name, an ordinal
    with no source paragraph, or a text-rail mismatch."""
    path = overrides_path(docx)
    if not path.is_file():
        return {}
    from pancratius.docx_inspect import read_rows

    def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        d = dict(pairs)
        if len(d) != len(pairs):
            raise ValueError(f"{path.name}: duplicate key in sidecar object")
        return d

    try:
        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_no_duplicate_keys)
    except json.JSONDecodeError as e:
        raise ValueError(f"{path.name}: not valid JSON — {e}") from e
    if not isinstance(raw, dict):
        raise ValueError(f"{path.name}: must be an object keyed by source ordinal")

    rows = {r.index: r for r in read_rows(docx)}
    out: dict[int, str] = {}
    for key, entry in raw.items():
        if not (key.isdigit() and str(int(key)) == key):
            raise ValueError(f"{path.name}: key {key!r} is not a canonical ordinal")
        ordinal = int(key)
        if not isinstance(entry, dict):
            raise ValueError(f"{path.name}: ordinal {ordinal} entry must be an object")
        source, text_sha = entry.get("source"), entry.get("text_sha")
        if not (isinstance(source, str) and source.strip()):
            raise ValueError(f"{path.name}: ordinal {ordinal} must name its canonical source")
        if not isinstance(text_sha, str):
            raise ValueError(f"{path.name}: ordinal {ordinal} is missing the text_sha rail")
        row = rows.get(ordinal)
        if row is None:
            raise ValueError(f"{path.name}: ordinal {ordinal} has no source paragraph in "
                             f"{docx.name} — the pin is stale; re-adjudicate or remove it")
        if not row.text.strip():
            raise ValueError(f"{path.name}: ordinal {ordinal} is a blank paragraph — "
                             f"a pin must land on quotation text")
        if paragraph_sha(row.text) != text_sha:
            raise ValueError(f"{path.name}: ordinal {ordinal} text drifted under the pin "
                             f"(rail {text_sha} != live {paragraph_sha(row.text)}) — "
                             f"re-adjudicate against the current text")
        out[ordinal] = source
    return out
