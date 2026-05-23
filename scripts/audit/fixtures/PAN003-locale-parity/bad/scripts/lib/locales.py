"""Fixture (PAN003-locale-parity / bad): Python locale SoT that DISAGREES with
the sibling src/lib/locales.ts (only "ru" here, but TS lists "ru","en"). The
audit execs this module, so it must import."""

LOCALES: tuple[str, ...] = ("ru",)
DEFAULT_LOCALE: str = "ru"
