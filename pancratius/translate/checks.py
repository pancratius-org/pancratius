"""Deterministic, model-free validation of a finished translation map.

These run before (and instead of) any LLM QA on the cheap, mechanical failures:
a unit the model forgot, an empty answer, residual Cyrillic (text echoed back
untranslated), or broken inline markup. Findings are tiered so the CLI and the
revise stage can act on the serious ones and merely report the cosmetic ones.

What is NOT checked here is faithfulness/meaning — that needs the source-aware
LLM critic in ``revise.py``. This module only knows mechanical invariants.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

from pancratius.translate.document import Document, Translations, UnitId

_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
_LINK_RE = re.compile(r"\]\(")
# Two scripts welded into one token ("HaveЙ"): a Latin letter directly touching a
# Cyrillic one. A clean gloss keeps the scripts apart with a space/quote/paren, so
# the adjacency never occurs.
_MIXED_SCRIPT_RE = re.compile(r"[A-Za-z][А-Яа-яЁё]|[А-Яа-яЁё][A-Za-z]")


class Severity(IntEnum):
    """Ordered so callers can filter with ``>=``. CRITICAL blocks a write."""

    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


@dataclass(frozen=True, slots=True)
class Finding:
    severity: Severity
    code: str
    message: str
    unit_id: UnitId | None = None


def _bold_runs(text: str) -> int:
    return text.count("**")


def check_translation(
    document: Document,
    translations: Translations,
    *,
    en_fm: Mapping[str, Any] | None = None,
) -> list[Finding]:
    findings: list[Finding] = []
    if en_fm is not None:
        findings.extend(_frontmatter_findings(en_fm))
    for unit in document.units:
        target = translations.get(unit.id)
        if target is None:
            findings.append(
                Finding(Severity.CRITICAL, "missing", "no translation returned", unit.id)
            )
            continue
        if not target.strip():
            findings.append(Finding(Severity.HIGH, "empty", "translation is empty", unit.id))
            continue
        if _CYRILLIC_RE.search(target):
            count = len(_CYRILLIC_RE.findall(target))
            findings.append(
                Finding(
                    Severity.MEDIUM,
                    "residual_cyrillic",
                    f"{count} Cyrillic char(s) remain in the translation",
                    unit.id,
                )
            )
        if target.strip() == unit.source.strip() and _CYRILLIC_RE.search(unit.source):
            findings.append(
                Finding(Severity.HIGH, "echoed", "translation equals the source", unit.id)
            )
        if _bold_runs(target) % 2:
            findings.append(
                Finding(Severity.HIGH, "unbalanced_bold", "odd number of '**' markers", unit.id)
            )
        src_links, tgt_links = len(_LINK_RE.findall(unit.source)), len(_LINK_RE.findall(target))
        if tgt_links < src_links:
            findings.append(
                Finding(
                    Severity.MEDIUM,
                    "dropped_link",
                    f"source has {src_links} link(s), translation has {tgt_links}",
                    unit.id,
                )
            )
        if _MIXED_SCRIPT_RE.search(target):
            findings.append(
                Finding(
                    Severity.MEDIUM,
                    "mixed_script",
                    "a token welds Cyrillic into Latin (e.g. a botched transliteration)",
                    unit.id,
                )
            )
        # A non-trivial, non-Cyrillic source returned byte-for-byte is a likely
        # passthrough (echoed catches the Cyrillic case; the guards skip numbers,
        # "OK", and markdown-only lines that legitimately survive untranslated).
        src = unit.source.strip()
        if (
            target.strip() == src
            and not _CYRILLIC_RE.search(unit.source)
            and len(src) > 3
        ):
            findings.append(
                Finding(Severity.MEDIUM, "byte_equal", "translation is byte-identical to a non-Cyrillic source", unit.id)
            )
    return findings


def _frontmatter_findings(en_fm: Mapping[str, Any]) -> list[Finding]:
    """Cyrillic left in the assembled EN frontmatter — a title/description/tag the
    profile pass returned untranslated. Frontmatter has no unit id, so these carry
    none; the field name lives in the message."""
    findings: list[Finding] = []
    fields: list[tuple[str, object]] = [
        ("title", en_fm.get("title")),
        ("description", en_fm.get("description")),
    ]
    tags = en_fm.get("tags")
    if isinstance(tags, list):
        fields += [(f"tag[{i}]", tag) for i, tag in enumerate(tags)]
    for name, value in fields:
        if isinstance(value, str) and _CYRILLIC_RE.search(value):
            findings.append(
                Finding(Severity.MEDIUM, "frontmatter_cyrillic", f"Cyrillic remains in frontmatter {name}", None)
            )
    return findings


def worst_severity(findings: list[Finding]) -> Severity | None:
    return max((f.severity for f in findings), default=None)
