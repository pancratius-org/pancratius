// Canonical locale list and default locale — the single TS source of truth.
//
// The site ships RU (at /ru/) and EN (at /en/); every locale is prefixed. This
// module is the one place the locale *codes* and their order live. `src/lib/i18n/` builds
// the full `LOCALE_META` registry on top of this list (labels, names, URL
// prefixes, OG codes, fallback chain); everything else derives from there.
//
// This file is PURE TypeScript on purpose — it imports nothing from
// `astro:content`, so `astro.config.ts` and `src/content.config.ts` (both of
// which run outside the content layer) can import it too. The matching Python
// source is `pancratius/locales.py`; `audit/python/locales.py` is the
// cross-language guard that keeps the two copies in agreement.
//
// This mirrors the work-kind ↔ segment pattern in `src/lib/kinds.ts`.

/** All locale codes, in canonical (display) order. The default locale leads. */
export const LOCALES = ["ru", "en"] as const;

/** A single locale code. */
export type Locale = typeof LOCALES[number];

/** The default locale — the apex `/` redirect target and the JSON-LD series home. */
export const DEFAULT_LOCALE = "ru" satisfies Locale;
