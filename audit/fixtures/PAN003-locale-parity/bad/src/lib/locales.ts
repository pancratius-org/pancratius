// Fixture (PAN003-locale-parity / bad): TS locale SoT that DISAGREES with the
// sibling pancratius/locales.py (TS has two locales, Python has one) — the
// cross-language audit must fire.
export const LOCALES = ["ru", "en"] as const;
export type Locale = (typeof LOCALES)[number];
export const DEFAULT_LOCALE: Locale = "ru";
