from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from pancratius.locales import Locale
from pancratius.localization._yaml import YAMLMapping, as_mapping


@dataclass(frozen=True, slots=True)
class TermReplacement:
    forbidden: str
    canonical: str
    insensitive: bool


type TermReplacements = tuple[TermReplacement, ...]


def apply_term_replacements(text: str, terms: TermReplacements) -> str:
    for term in terms:
        flags = re.IGNORECASE if term.insensitive else 0
        text = re.sub(
            rf"\b{re.escape(term.forbidden)}\b",
            lambda _, c=term.canonical: c,
            text,
            flags=flags,
        )
    return text


def load_term_replacements(path: Path, locale: Locale) -> TermReplacements:
    try:
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        return ()
    terms: list[TermReplacement] = []
    for entry in _term_entries(raw):
        locale_block = as_mapping(entry.get(locale))
        if locale_block is None:
            continue
        use = locale_block.get("use")
        avoid = locale_block.get("avoid")
        enforcement = locale_block.get("enforcement")
        if not isinstance(use, str) or not isinstance(avoid, list):
            continue
        if enforcement not in {"denylist", "flag"}:
            continue
        insensitive = locale_block.get("match") == "insensitive"
        canonical = use.split(" / ")[0].strip()
        terms.extend(
            TermReplacement(item, canonical, insensitive)
            for item in avoid
            if isinstance(item, str)
        )
    return tuple(terms)


def _term_entries(raw: object) -> list[YAMLMapping]:
    data = as_mapping(raw)
    if data is None:
        return []
    terms = data.get("terms")
    if not isinstance(terms, list):
        return []
    entries: list[YAMLMapping] = []
    for item in terms:
        entry = as_mapping(item)
        if entry is not None:
            entries.append(entry)
    return entries
