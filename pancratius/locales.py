"""Canonical locale list and default locale (Python side).

This mirrors ``src/lib/locales.ts``. Python cannot import the TS module and the
config can't import Python, so the list necessarily exists once per language.
``audit/python/locales.py`` is the cross-language guard: it asserts this list
and default equal the ``LOCALES`` / ``DEFAULT_LOCALE`` in ``src/lib/locales.ts``.
"""

from __future__ import annotations

from typing import Literal, TypeGuard

type Locale = Literal["ru", "en"]

# All locale codes, in canonical (display) order. The default locale leads.
LOCALES: tuple[Locale, ...] = ("ru", "en")

# The default locale — the apex `/` redirect target. Every locale is prefixed.
DEFAULT_LOCALE: Locale = "ru"


def is_locale(value: str) -> TypeGuard[Locale]:
    """Return whether an untrusted string is one of the configured locales."""
    return value in LOCALES
