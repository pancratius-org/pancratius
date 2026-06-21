"""Structured-output schemas for the cover recon and QA calls.

Pattern mirrors pancratius/translate/schema.py: strict JSON schemas constrain
the model output so parsing is reliable and field names condition the model.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pancratius.cover.models import (
    CoverElement,
    ElementRole,
    QaDiscrepancy,
    QaResult,
    QaVerdict,
    ReconResult,
)

type JsonSchema = dict[str, Any]
type ResponseFormat = JsonSchema


def _json_schema(name: str, schema: JsonSchema) -> ResponseFormat:
    return {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}}


def recon_format() -> ResponseFormat:
    """Schema for the load-bearing recon call.

    Recon both FINDS and TRANSLATES: each element carries its verbatim Russian,
    the recon model's English for it, its role, and whether the text is baked into
    the artwork. Generation then renders these exact strings, so it can neither miss
    an element nor invent a wrong translation. ``displayed_title`` is kept for logs.
    """
    element = {
        "type": "object",
        "additionalProperties": False,
        "required": ["role", "russian", "english", "art_baked"],
        "properties": {
            "role": {
                "type": "string",
                "enum": [r.value for r in ElementRole],
                "description": "The role of this text element on the cover.",
            },
            "russian": {
                "type": "string",
                "description": "The verbatim Russian text as displayed on the cover.",
            },
            "english": {
                "type": "string",
                "description": (
                    "Your faithful English translation of this element's Russian "
                    "(translate it yourself; e.g. «Система дефицита» → 'System of "
                    "Scarcity', «в его власти» → 'In His Power'). For a name, "
                    "transliterate."
                ),
            },
            "art_baked": {
                "type": "boolean",
                "description": (
                    "True if the text is painted INTO the artwork (a coin emblem, a "
                    "banner, decorative lettering) rather than overlaid as a caption."
                ),
            },
        },
    }
    return _json_schema(
        "cover_recon",
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["elements", "displayed_title"],
            "properties": {
                "elements": {
                    "type": "array",
                    "description": (
                        "EVERY text element visible on the cover, in reading order — "
                        "title, subtitle, author, taglines, and any text in the artwork. "
                        "Do not omit any."
                    ),
                    "items": element,
                },
                "displayed_title": {
                    "type": "string",
                    "description": (
                        "The title as it actually appears on the cover (may be shorter "
                        "than the full catalogue title; e.g. just 'Мамона' not 'Книга 50. "
                        "Мамона. Почему ты в его власти…')."
                    ),
                },
            },
        },
    )


def qa_format() -> ResponseFormat:
    """Schema for the vision QA call (comparing RU source with EN output).

    ``verdict`` is either ``pass`` (no issues) or ``fail`` (discrepancies found).
    On fail, ``discrepancies`` lists concrete problems; on pass the array is empty.
    """
    discrepancy = {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "description", "in_artwork"],
        "properties": {
            "kind": {
                "type": "string",
                "enum": [
                    "cyrillic_left",   # Russian/Cyrillic text not translated
                    "artwork_changed", # background or artwork visually altered
                    "text_dropped",    # a text element from the source is missing
                    "author_wrong",    # author not rendered as "Sergei Pancratius"
                    "other",
                ],
                "description": "Category of the defect.",
            },
            "description": {
                "type": "string",
                "description": "Concrete description of the defect (quote the offending text).",
            },
            "in_artwork": {
                "type": "boolean",
                "description": (
                    "True if the offending text is painted INTO the artwork "
                    "(an emblem, coin, banner, or decorative lettering); False if "
                    "it is an overlay caption (title, subtitle, author, or tagline)."
                ),
            },
        },
    }
    return _json_schema(
        "cover_qa",
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["verdict", "discrepancies"],
            "properties": {
                "verdict": {
                    "type": "string",
                    "enum": ["pass", "fail"],
                    "description": "'pass' if the EN cover is correct, 'fail' if issues were found.",
                },
                "discrepancies": {
                    "type": "array",
                    "description": "Concrete defects; empty when verdict is 'pass'.",
                    "items": discrepancy,
                },
            },
        },
    )


def _extract_json(text: str) -> str:
    """Extract the JSON object from text that may be wrapped in markdown fences.

    Some vision models return ```json ... ``` even when asked for raw JSON.
    Falls back to scanning for the first BALANCED JSON object using raw_decode
    so extra braces after the object don't produce a greedy-span mismatch.
    """
    text = text.strip()
    # Strip ```json ... ``` or ``` ... ``` fences
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        candidate = m.group(1)
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass

    # Walk the string to find the first position that starts a valid JSON object;
    # raw_decode stops at the closing brace so extra trailing content is ignored.
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            _obj, end = decoder.raw_decode(text, i)
            return text[i:end]  # the exact balanced span
        except json.JSONDecodeError:
            continue
    return text


def _coerce_role(role: str) -> ElementRole:
    """Map a recon role string to ElementRole; unknown roles fall back to OTHER."""
    try:
        return ElementRole(role)
    except ValueError:
        return ElementRole.OTHER


def parse_recon(text: str) -> ReconResult:
    """Parse a recon JSON reply straight into a `ReconResult`.

    Each element carries its Russian, the recon model's English, role, and
    art-baked flag. An element missing ``russian`` or ``english`` is skipped (no
    usable replacement pair); ``art_baked`` defaults to False when absent. Raises
    on a reply that is not a JSON object (the caller decides how to degrade).
    """
    data = json.loads(_extract_json(text))
    if not isinstance(data, dict):
        raise ValueError("recon reply was not a JSON object")
    elements: list[CoverElement] = []
    for item in data.get("elements") or []:
        match item:
            case {"role": str(role), "russian": str(russian), "english": str(english)}:
                elements.append(
                    CoverElement(
                        role=_coerce_role(role),
                        russian=russian,
                        english=english,
                        art_baked=bool(item.get("art_baked", False)),
                    )
                )
    displayed = data.get("displayed_title") or ""
    if not isinstance(displayed, str):
        displayed = ""
    return ReconResult(elements=tuple(elements), displayed_title=displayed, raw_json=text)


def parse_qa(text: str) -> QaResult:
    """Parse a QA JSON reply straight into a `QaResult`.

    Verdict is `FAIL` unless the model explicitly said "pass" (fail-closed on an
    unknown verdict). Malformed discrepancies are skipped. Raises on a reply that
    is not a JSON object — the caller then fails closed, never inferring PASS.
    """
    data = json.loads(_extract_json(text))
    if not isinstance(data, dict):
        raise ValueError("QA reply was not a JSON object")
    verdict = QaVerdict.PASS if data.get("verdict") == "pass" else QaVerdict.FAIL
    discrepancies: list[QaDiscrepancy] = []
    for item in data.get("discrepancies") or []:
        match item:
            case {"kind": str(kind), "description": str(desc)}:
                discrepancies.append(
                    QaDiscrepancy(
                        kind=kind,
                        description=desc,
                        in_artwork=bool(item.get("in_artwork", False)),
                    )
                )
    return QaResult(verdict=verdict, discrepancies=tuple(discrepancies), raw_json=text)
