import { DEFAULT_LOCALE } from "../locales.ts";
import { originFor } from "../origins.ts";

/**
 * Origin baked into the absolute image URLs of the public corpus surfaces (the
 * `all-md.zip` archive and the per-work Markdown/TXT download derivations). The
 * archive is the canonical bilingual corpus dump, so it anchors on the
 * default-locale (`.ru`) origin. See `../origins.ts` for the per-locale map.
 */
export function publicationOrigin(): string {
  return originFor(DEFAULT_LOCALE);
}
