// Fixture (PAN003-locale-parity / good): TS locale SoT that AGREES with the
// sibling scripts/lib/locales.py — the cross-language audit must stay silent.
export const LOCALES = ["ru", "en"] as const;
export type Locale = (typeof LOCALES)[number];
export const DEFAULT_LOCALE: Locale = "ru";
