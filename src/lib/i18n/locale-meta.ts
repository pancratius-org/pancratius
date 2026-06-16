import { DEFAULT_LOCALE, LOCALES, type Locale } from "../locales.ts";

export interface LocaleMeta {
  /** Short label rendered in chrome (header nav, footer, switcher). */
  label: string;
  /**
   * Long-form locale names, by the locale they are *displayed in*. Read as
   * `LOCALE_META[targetLocale].name[uiLocale]` for "the name of `target`,
   * written in `ui`'s language".
   */
  name: Record<Locale, string>;
  /**
   * URL prefix segment (no slashes). Every locale is prefixed; the apex `/` is
   * a host-decided redirect, not a locale home. Which locale is canonical is
   * owned by `DEFAULT_LOCALE`, not by a flag here.
   */
  urlPrefix: string;
  /** Open Graph `og:locale` code, e.g. "ru_RU". */
  ogLocale: string;
  /** Display name of the site in this locale (EN never uses the Cyrillic spelling). */
  siteLabel: string;
  /** Locale to fall back to for derived display data when this one is absent. */
  fallback: Locale;
}

export const LOCALE_META: Record<Locale, LocaleMeta> = {
  ru: {
    label: "RU",
    name: { ru: "Русский", en: "Russian" },
    urlPrefix: "ru",
    ogLocale: "ru_RU",
    siteLabel: "Панкратиус",
    fallback: DEFAULT_LOCALE,
  },
  en: {
    label: "EN",
    name: { ru: "Английский", en: "English" },
    urlPrefix: "en",
    ogLocale: "en_US",
    siteLabel: "Pancratius",
    fallback: DEFAULT_LOCALE,
  },
};

/** Names rendered in UI chrome (header nav, footer, switcher). */
export const LOCALE_LABEL: Record<Locale, string> = Object.fromEntries(
  LOCALES.map(locale => [locale, LOCALE_META[locale].label]),
) as Record<Locale, string>;

/**
 * Long-form locale name for ARIA. Read as `LOCALE_NAME[uiLocale][targetLocale]`
 * — "the name of `target`, written in `ui`'s language".
 */
export const LOCALE_NAME: Record<Locale, Record<Locale, string>> = Object.fromEntries(
  LOCALES.map(ui => [
    ui,
    Object.fromEntries(LOCALES.map(target => [target, LOCALE_META[target].name[ui]])),
  ]),
) as Record<Locale, Record<Locale, string>>;

/** Locale whose URL prefix leads `path` (`/ru/…` → ru), or null when none does. */
export function localeFromPrefix(path: string): Locale | null {
  const segment = path.split("/")[1];
  return LOCALES.find(locale => LOCALE_META[locale].urlPrefix === segment) ?? null;
}
