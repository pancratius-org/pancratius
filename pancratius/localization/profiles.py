from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pancratius.locales import Locale

type QuoteKind = Literal["double", "single"]
type Script = Literal["russian-cyrillic", "latin"]


@dataclass(frozen=True, slots=True)
class QuoteMarks:
    opening: str
    closing: str


@dataclass(frozen=True, slots=True)
class LocaleProfile:
    locale: Locale
    language_name: str
    script: Script
    youtube_keys: tuple[str, ...]
    double_quotes: QuoteMarks
    single_quotes: QuoteMarks

    def quote_marks(self, kind: QuoteKind) -> QuoteMarks:
        return self.double_quotes if kind == "double" else self.single_quotes


_PROFILES: dict[Locale, LocaleProfile] = {
    "ru": LocaleProfile(
        locale="ru",
        language_name="Russian",
        script="russian-cyrillic",
        youtube_keys=("ru",),
        double_quotes=QuoteMarks("«", "»"),
        single_quotes=QuoteMarks("'", "'"),
    ),
    "en": LocaleProfile(
        locale="en",
        language_name="English",
        script="latin",
        youtube_keys=("en-US", "en", "en-GB"),
        double_quotes=QuoteMarks("“", "”"),
        single_quotes=QuoteMarks("‘", "’"),
    ),
}


def locale_profile(locale: Locale) -> LocaleProfile:
    return _PROFILES[locale]


def youtube_keys_for_locale(locale: Locale) -> tuple[str, ...]:
    return locale_profile(locale).youtube_keys
