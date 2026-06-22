"""Structured-output schemas for image recon and QA calls."""

from __future__ import annotations

import json
import re
from typing import Any

from pancratius.image_translation.models import (
    DetectedText,
    ImageReconResult,
    QaDiscrepancy,
    QaResult,
    QaVerdict,
    TextRole,
)

type JsonSchema = dict[str, Any]
type ResponseFormat = JsonSchema


def _json_schema(name: str, schema: JsonSchema) -> ResponseFormat:
    return {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}}


def recon_format() -> ResponseFormat:
    """Schema for the load-bearing recon call."""
    element = {
        "type": "object",
        "additionalProperties": False,
        "required": ["role", "source_text", "target_text", "embedded"],
        "properties": {
            "role": {
                "type": "string",
                "enum": [r.value for r in TextRole],
                "description": "The generic role of this text element in the image.",
            },
            "source_text": {
                "type": "string",
                "description": "The verbatim source-language text as displayed in the image.",
            },
            "target_text": {
                "type": "string",
                "description": "Your faithful target-language translation of this text element.",
            },
            "embedded": {
                "type": "boolean",
                "description": "True when the text is painted into the artwork rather than overlaid.",
            },
        },
    }
    return _json_schema(
        "image_text_recon",
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["elements", "primary_text"],
            "properties": {
                "elements": {
                    "type": "array",
                    "description": "Every visible text element in reading order.",
                    "items": element,
                },
                "primary_text": {
                    "type": "string",
                    "description": "The most prominent/dominant text as it appears in the source image.",
                },
            },
        },
    )


def qa_format() -> ResponseFormat:
    """Schema for the vision QA call comparing source and translated images."""
    discrepancy = {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "description", "embedded"],
        "properties": {
            "kind": {
                "type": "string",
                "enum": [
                    "source_text_left",
                    "artwork_changed",
                    "text_dropped",
                    "wrong_text",
                    "other",
                ],
                "description": "Category of the defect.",
            },
            "description": {
                "type": "string",
                "description": "Concrete description of the defect.",
            },
            "embedded": {
                "type": "boolean",
                "description": "True if the offending text is painted into the artwork.",
            },
        },
    }
    return _json_schema(
        "image_text_qa",
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["verdict", "discrepancies"],
            "properties": {
                "verdict": {
                    "type": "string",
                    "enum": ["pass", "fail"],
                    "description": "'pass' if the translated image is correct, otherwise 'fail'.",
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
    """Extract a JSON object from a possibly fenced or prefixed model reply."""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        candidate = m.group(1)
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass

    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            _obj, end = decoder.raw_decode(text, i)
            return text[i:end]
        except json.JSONDecodeError:
            continue
    return text


def _coerce_role(role: str) -> TextRole:
    legacy = {
        "title": TextRole.PRIMARY,
        "subtitle": TextRole.SECONDARY,
        "author": TextRole.CREDIT,
        "creator": TextRole.CREDIT,
        "byline": TextRole.CREDIT,
    }
    if role in legacy:
        return legacy[role]
    try:
        return TextRole(role)
    except ValueError:
        return TextRole.OTHER


def parse_recon(text: str) -> ImageReconResult:
    """Parse a recon JSON reply into an `ImageReconResult`."""
    data = json.loads(_extract_json(text))
    if not isinstance(data, dict):
        raise ValueError("recon reply was not a JSON object")
    elements: list[DetectedText] = []
    for item in data.get("elements") or []:
        if not isinstance(item, dict):
            continue
        source = item.get("source_text", item.get("russian"))
        target = item.get("target_text", item.get("english"))
        role = item.get("role")
        if not isinstance(source, str) or not isinstance(target, str) or not isinstance(role, str):
            continue
        elements.append(
            DetectedText(
                role=_coerce_role(role),
                source=source,
                suggested_target=target,
                embedded=bool(item.get("embedded", item.get("art_baked", False))),
            )
        )
    primary = data.get("primary_text", data.get("displayed_title", ""))
    if not isinstance(primary, str):
        primary = ""
    return ImageReconResult(elements=tuple(elements), primary_text=primary, raw_json=text)


def parse_qa(text: str) -> QaResult:
    """Parse a QA JSON reply. Unknown verdicts fail closed."""
    data = json.loads(_extract_json(text))
    if not isinstance(data, dict):
        raise ValueError("QA reply was not a JSON object")
    verdict = QaVerdict.PASS if data.get("verdict") == "pass" else QaVerdict.FAIL
    discrepancies: list[QaDiscrepancy] = []
    for item in data.get("discrepancies") or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        desc = item.get("description")
        if not isinstance(kind, str) or not isinstance(desc, str):
            continue
        if kind == "cyrillic_left":
            kind = "source_text_left"
        elif kind == "author_wrong":
            kind = "wrong_text"
        discrepancies.append(
            QaDiscrepancy(
                kind=kind,
                description=desc,
                embedded=bool(item.get("embedded", item.get("in_artwork", False))),
            )
        )
    return QaResult(verdict=verdict, discrepancies=tuple(discrepancies), raw_json=text)
