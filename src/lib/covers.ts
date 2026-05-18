// Cover URL resolution for routes that need an absolute OG / JSON-LD image.
//
// `BookCover.astro` resolves cover assets via Vite's `import.meta.glob` for
// the rendered `<img>`; routes also need that URL when building SEO metadata.
// Centralising the glob here keeps the resolution rule in one place.

import type { Locale } from "./i18n";
import { COLLECTION_OF, resolveCover, type WorkPair } from "./works";

const COVER_URLS = import.meta.glob<string>(
  "/content/**/cover.*.{jpg,jpeg,png,webp,avif,svg}",
  { eager: true, query: "?url", import: "default" },
);

/** The asset URL for a work's cover in the given locale, or null when absent. */
export async function coverAssetUrl(pair: WorkPair, locale: Locale): Promise<string | null> {
  const cover = await resolveCover(pair, locale);
  if (!cover) return null;
  const workFolder = pair.ru.id.split("--")[0];
  const segment = COLLECTION_OF[pair.kind];
  const key = `/content/${segment}/${workFolder}/${cover.rel.replace(/^\.\//, "")}`;
  return COVER_URLS[key] ?? null;
}

/** Absolute URL of the cover asset, suitable for og:image / JSON-LD image. */
export async function coverAbsoluteUrl(
  site: URL | undefined,
  pair: WorkPair,
  locale: Locale,
): Promise<string | null> {
  const rel = await coverAssetUrl(pair, locale);
  if (!rel || !site) return rel;
  return new URL(rel, site).toString();
}
