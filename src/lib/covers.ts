// Cover URL resolution for routes that need an absolute OG / JSON-LD image.
//
// `BookCover.astro` resolves cover assets via Vite's `import.meta.glob` for
// the rendered `<img>`; routes also need that URL when building SEO metadata.
// Centralising the glob here keeps the resolution rule in one place.

import { DEFAULT_LOCALE, type Locale } from "./i18n";
import { COLLECTION_OF, resolveCover, type WorkPair } from "./works";

const COVER_URLS = import.meta.glob<string>(
  "/src/content/**/cover.*.{jpg,jpeg,png,webp,avif,svg}",
  { eager: true, query: "?url", import: "default" },
);

/** The asset URL for a work's cover in the given locale, or null when absent. */
export function coverAssetUrl(pair: WorkPair, locale: Locale): string | null {
  const cover = resolveCover(pair, locale);
  if (!cover) return null;
  const workFolder = pair.entries[DEFAULT_LOCALE]!.id.split("--")[0];
  const segment = COLLECTION_OF[pair.kind];
  const key = `/src/content/${segment}/${workFolder}/${cover.rel.replace(/^\.\//, "")}`;
  return COVER_URLS[key] ?? null;
}

/** Absolute URL of the cover asset, suitable for og:image / JSON-LD image. */
export function coverAbsoluteUrl(
  site: URL | undefined,
  pair: WorkPair,
  locale: Locale,
): string | null {
  const rel = coverAssetUrl(pair, locale);
  if (!rel || !site) return rel;
  return new URL(rel, site).toString();
}
