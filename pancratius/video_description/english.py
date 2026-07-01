"""Normalize an English localization to the library's conventions.

The author's own en-US text is faithful but written for YouTube: it uses
non-canonical terminology ("Holy Russia", "Pankratius") and straight quotes.
This pass makes it clear the two English audits — PAN027 (canonical terminology
from ``data/translation-glossary.yaml``) and PAN026 (American curly quotes facing
the right way) — deterministically, so an unattended sync's English is
site-conformant, not just the author's raw upload.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# A quote opens after a boundary and closes otherwise — the smart-quote rule
# applied across the whole text, so a quotation spanning paragraphs closes with ”
# instead of a backwards “. The boundary set matches PAN026's own OPEN_PREV
# (whitespace, opening brackets, dashes, a colon or slash, Markdown emphasis) so
# the normalizer and the auditor never disagree on which way a quote faces.
_OPENS_AFTER = re.compile(r"[\s([{<«—–\-*_~:/]")


@dataclass(frozen=True, slots=True)
class TermReplacement:
    """One canonical-terminology rewrite: replace ``forbidden`` with
    ``canonical``, case-insensitively when the glossary term is so marked."""
    forbidden: str
    canonical: str
    insensitive: bool


type TermReplacements = tuple[TermReplacement, ...]


def normalize_english(text: str, terms: TermReplacements) -> str:
    """Apply canonical terminology, then curl straight double quotes."""
    return _curl_double_quotes(_apply_terms(text, terms))


def _apply_terms(text: str, terms: TermReplacements) -> str:
    for term in terms:
        flags = re.IGNORECASE if term.insensitive else 0
        # A lambda replacement (not a template) so a canonical with \ or \g<…> is
        # inserted literally, not read as a backreference.
        text = re.sub(rf"\b{re.escape(term.forbidden)}\b", lambda _, c=term.canonical: c, text, flags=flags)
    return text


def _curl_double_quotes(text: str) -> str:
    out: list[str] = []
    for i, ch in enumerate(text):
        if ch != '"':
            out.append(ch)
            continue
        prev = text[i - 1] if i else ""
        out.append("“" if prev == "" or _OPENS_AFTER.match(prev) else "”")
    return "".join(out)
