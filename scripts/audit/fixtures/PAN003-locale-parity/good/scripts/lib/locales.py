"""Fixture (PAN003-locale-parity / good): Python locale SoT that AGREES with the
sibling src/lib/locales.ts. The audit execs this module, so it must import."""

LOCALES: tuple[str, ...] = ("ru", "en")
DEFAULT_LOCALE: str = "ru"
