// Cover URL resolution for routes that need an absolute OG / JSON-LD image.
//
// `BookCover.astro` resolves cover assets via Vite's `import.meta.glob` for
// the rendered `<img>`; routes also need that URL when building SEO metadata.
// Centralising the glob here keeps the resolution rule in one place.

import type { Locale } from "./i18n";
import { workCoverKey } from "./cover-assets";
import { originFor } from "./origins";
import type { WorkPair } from "./works";

const COVER_URLS = import.meta.glob<string>(
  "/src/content/**/cover.*.{jpg,jpeg,png,webp,avif,svg}",
  { eager: true, query: "?url", import: "default" },
);

/** The asset URL for a work's cover in the given locale, or null when absent. */
export function coverAssetUrl(pair: WorkPair, locale: Locale): string | null {
  const key = workCoverKey(pair, locale);
  return key ? COVER_URLS[key] ?? null : null;
}

/** Absolute URL of the cover asset on the page locale's origin, for og:image / JSON-LD image. */
export function coverAbsoluteUrl(pair: WorkPair, locale: Locale): string | null {
  const rel = coverAssetUrl(pair, locale);
  if (!rel) return null;
  return new URL(rel, originFor(locale)).toString();
}
