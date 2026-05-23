"""Canonical locale list and default locale (Python side).

This mirrors ``src/lib/locales.ts``. Python cannot import the TS module and the
config can't import Python, so the list necessarily exists once per language.
``scripts/audit/locales.py`` is the cross-language guard: it asserts this list
and default equal the ``LOCALES`` / ``DEFAULT_LOCALE`` in ``src/lib/locales.ts``.
"""

from __future__ import annotations

# All locale codes, in canonical (display) order. The default locale leads.
LOCALES: tuple[str, ...] = ("ru", "en")

# The default (unprefixed, canonical) locale.
DEFAULT_LOCALE: str = "ru"
