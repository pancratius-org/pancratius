from __future__ import annotations

import re
from typing import assert_never

from pancratius.locales import Locale
from pancratius.localization.profiles import locale_profile

_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)
_RU_CYRILLIC = re.compile(r"[а-яёА-ЯЁ]")
_UKRAINIAN = re.compile(r"[іїєґІЇЄҐ]")


def drifted_off_language(text: str, locale: Locale) -> bool:
    letters = len(_LETTER.findall(text))
    if letters < 8:
        return False
    russian = len(_RU_CYRILLIC.findall(text)) / letters
    match locale_profile(locale).script:
        case "russian-cyrillic":
            return bool(_UKRAINIAN.search(text)) or russian < 0.6
        case "latin":
            return russian > 0.4
        case script:
            assert_never(script)
