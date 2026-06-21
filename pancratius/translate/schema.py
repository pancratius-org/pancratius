"""Structured-output contracts (OpenRouter ``response_format`` JSON schemas).

The model is constrained by a schema, not coaxed by prose. Two things the schema
buys beyond "valid JSON": the ``id`` is an ``enum`` of exactly the shown unit ids,
so an invented or continued id is structurally impossible (coverage — every id
present — is still checked in code and retried); and the field names + short
``description``s name each field's intent so the model is conditioned by them. The
full translation contract lives once in the prompt (``STYLE_GUIDE``), not here.

Pattern mirrors the lineation-core teacher panel: a strict, object-wrapped array
(a top-level array is not allowed under strict decoding), ``additionalProperties:
false`` throughout.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from pancratius.translate.document import UnitId

# A JSON-Schema fragment / OpenRouter ``response_format`` blob. Open by nature —
# these mirror the wire schema, so a TypedDict would only fight the structure.
type JsonSchema = dict[str, Any]
type ResponseFormat = JsonSchema


def _json_schema(name: str, schema: JsonSchema) -> ResponseFormat:
    return {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}}


def translation_format(unit_ids: Sequence[UnitId]) -> ResponseFormat:
    """Schema for a batch of translated units: ``{"translations": [{id, english}]}``.
    ``id`` is restricted to the shown ids; ``english`` carries the contract."""
    return _json_schema(
        "translations",
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["translations"],
            "properties": {
                "translations": {
                    "type": "array",
                    "description": "One entry per source unit you were asked to translate.",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["id", "english"],
                        "properties": {
                            "id": {
                                "type": "string",
                                "enum": list(unit_ids),
                                "description": "The exact id of the source unit, copied verbatim.",
                            },
                            "english": {
                                "type": "string",
                                "description": "The faithful English translation of that unit's Russian source.",
                            },
                        },
                    },
                }
            },
        },
    )


def parse_translations(text: str) -> dict[UnitId, str]:
    """Read a ``{"translations": [{id, english}]}`` reply into an id→text map,
    skipping rows the model malformed (missing or non-string ``id``/``english``)."""
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("translation reply was not a JSON object")
    rows = data.get("translations")
    if not isinstance(rows, list):
        raise ValueError("translation reply had no 'translations' array")
    out: dict[UnitId, str] = {}
    for row in rows:
        match row:
            case {"id": str(unit_id), "english": str(english)}:
                # Each unit is one source line, so the translation must be one line:
                # a stray model-emitted newline would render as a second physical
                # line and re-parse as an extra unit (structure drift).
                out[unit_id] = " ".join(english.splitlines())
    return out


def profile_format() -> ResponseFormat:
    """Schema for the per-book brief. Named, described fields condition the model
    to produce each part with the right intent (English title, locked terms, …)."""
    persona = {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "voice"],
        "properties": {
            "name": {"type": "string", "description": "Who speaks (e.g. the Creator, the narrator)."},
            "voice": {"type": "string", "description": "Their register and how they address the reader."},
        },
    }
    term = {
        "type": "object",
        "additionalProperties": False,
        "required": ["source", "target", "note", "locked"],
        "properties": {
            "source": {"type": "string", "description": "The Russian name or recurring term."},
            "target": {"type": "string", "description": "Its fixed English rendering."},
            "note": {"type": "string", "description": "Why, or how to use it (may be empty)."},
            "locked": {"type": "boolean", "description": "True if this rendering must never vary."},
        },
    }
    return _json_schema(
        "book_brief",
        {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "title_en", "description_en", "tags_en", "summary",
                "register", "personas", "terms", "recurring",
            ],
            "properties": {
                "title_en": {"type": "string", "description": "Faithful English title (not literal-clumsy)."},
                "description_en": {"type": "string", "description": "English rendering of the description."},
                "tags_en": {
                    "type": "array", "items": {"type": "string"},
                    "description": "The provided tags, each rendered in English.",
                },
                "summary": {"type": "string", "description": "2-4 sentences: argument, arc and stakes."},
                "register": {"type": "string", "description": "One line on voice/register and who speaks."},
                "personas": {"type": "array", "items": persona, "description": "Recurring speakers/characters."},
                "terms": {
                    "type": "array", "items": term,
                    "description": "Names and recurring terms whose English must stay consistent.",
                },
                "recurring": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Recurring symbolic phrases to translate consistently.",
                },
            },
        },
    )
