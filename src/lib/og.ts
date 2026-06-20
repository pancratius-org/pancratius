// Social-card (Open Graph) images.
//
// The image renders ALONGSIDE the platform's own title + description (WhatsApp,
// Telegram, X, etc. draw those from og:title/og:description), so the image must be a
// pure visual hook — it never repeats the title. That makes the right image obvious:
//
// - Content (book, poem, project, sub-page, video): its own cover / thumbnail is the
//   og:image. Messaging apps show it large, respecting aspect — a portrait cover
//   reads beautifully. Resolved per route on the resource locale's origin.
// - Cover-less surfaces (home, indexes, pages, search, messages): a committed brand
//   image; the platform's title chrome differentiates them. Per locale, because the
//   card carries the localized tagline.
// - Conceptosphere: a committed still of its graph.
//
// These are committed assets in `public/og/` (served as-is), not generated.

import type { Locale } from "./locales";
import { originFor } from "./origins";

/** Committed brand image for a locale (the card carries that locale's tagline). */
export function ogBrandPath(locale: Locale): string {
  return `/og/brand.${locale}.jpg`;
}
/** Committed still of the conceptosphere graph (locale-neutral). */
export const OG_CONCEPTOSPHERE_PATH = "/og/conceptosphere.jpg";

/** A root-relative og-image path as an absolute URL on the resource locale's origin. */
export function ogImageUrl(path: string, locale: Locale): string {
  return new URL(path, originFor(locale)).toString();
}
