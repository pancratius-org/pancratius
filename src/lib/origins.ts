// Per-locale canonical/SEO ORIGIN — the single source of truth for the domain a
// resource's canonical, hreflang, Open Graph, and JSON-LD URLs point at. Origin
// is a function of the RESOURCE's locale, never of the serving host: both
// domains are regional mirrors serving the identical full bilingual `dist/`, so
// this map is the SEO consolidation axis, not a per-language "serving home". A
// reader still switches language same-origin on whichever mirror reached them.
// RU = pancratius.ru (ccTLD, Russian audience + geo signal); EN = pancratius.org
// (gTLD, international face); a new locale defaults to the global `.org`.
//
// Pure TypeScript (imports nothing from `astro:content`) so `astro.config.ts`
// and route code can both import it, exactly like `locales.ts` / `kinds.ts`.
// Defaults keep local dev and CI zero-config; `PUBLIC_SITE_URL_<LOCALE>`
// overrides one origin per deploy (e.g. a preview origin). Typed against
// `Locale`, so adding a locale forces declaring its canonical origin here.

import { LOCALES, type Locale } from "./locales.ts";

const DEFAULT_ORIGIN: Record<Locale, string> = {
  ru: "https://pancratius.ru",
  en: "https://pancratius.org",
};

const LOCALE_ORIGIN: Record<Locale, string> = Object.fromEntries(
  LOCALES.map((locale): [Locale, string] => {
    const override = process.env[`PUBLIC_SITE_URL_${locale.toUpperCase()}`];
    return [locale, new URL(override ?? DEFAULT_ORIGIN[locale]).origin];
  }),
) as Record<Locale, string>;

/** Canonical origin (scheme + host, no trailing slash) for a resource in `locale`. */
export function originFor(locale: Locale): string {
  return LOCALE_ORIGIN[locale];
}
