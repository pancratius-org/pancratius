from __future__ import annotations

import re

from pancratius.locales import Locale
from pancratius.localization.glossary import TermReplacements, apply_term_replacements
from pancratius.localization.profiles import QuoteKind, QuoteMarks, locale_profile

_OPENS_AFTER = re.compile(r"[\s([{<«“‘—–\-*_~:/]")


def quote_marks(locale: Locale, kind: QuoteKind) -> QuoteMarks:
    return locale_profile(locale).quote_marks(kind)


def normalize_quotes(text: str, locale: Locale) -> str:
    profile = locale_profile(locale)
    text = normalize_literal_quotes(text, locale)
    text = _curl(text, '"', profile.double_quotes)
    return _curl_single_quotes(text, profile.single_quotes)


def normalize_literal_quotes(text: str, locale: Locale) -> str:
    profile = locale_profile(locale)
    return text.translate(str.maketrans({"«": profile.double_quotes.opening, "»": profile.double_quotes.closing}))


def normalize_locale_text(
    text: str,
    locale: Locale,
    terms: TermReplacements = (),
) -> str:
    return normalize_quotes(apply_term_replacements(text, terms), locale)


def _curl(text: str, raw: str, marks: QuoteMarks) -> str:
    out: list[str] = []
    for i, ch in enumerate(text):
        if ch != raw:
            out.append(ch)
            continue
        prev = text[i - 1] if i else ""
        out.append(marks.opening if prev == "" or _OPENS_AFTER.match(prev) else marks.closing)
    return "".join(out)


def _curl_single_quotes(text: str, marks: QuoteMarks) -> str:
    if marks.opening == "'" and marks.closing == "'":
        return text
    out: list[str] = []
    for i, ch in enumerate(text):
        if ch != "'":
            out.append(ch)
            continue
        prev = text[i - 1] if i else ""
        next_ch = text[i + 1] if i + 1 < len(text) else ""
        if _is_letter(prev) and _is_letter(next_ch):
            out.append(marks.closing)
        elif prev == "" or _OPENS_AFTER.match(prev):
            out.append(marks.opening)
        else:
            out.append(marks.closing)
    return "".join(out)


def _is_letter(value: str) -> bool:
    return bool(value) and (value.isalpha() or value == "’")
