import { SEGMENT_OF, type CorpusWorkKind, type RoutedKind } from "../kinds";
import type { Locale } from "../locales";
import type { DownloadFormat } from "../downloads";
import { LOCALE_META } from "./locale-meta";

/** Prefix a root-relative path with the locale segment (every locale is prefixed). */
export function localizePath(path: string, locale: Locale): string {
  if (!path.startsWith("/")) {
    throw new Error(`localizePath expects an absolute path, got ${JSON.stringify(path)}`);
  }
  return `/${LOCALE_META[locale].urlPrefix}${path}`;
}

/** Canonical routed-content URL for `(kind, slug)` in `locale`. Slug must be per-language. */
export function routedUrl(kind: RoutedKind, slug: string, locale: Locale): string {
  return localizePath(`/${SEGMENT_OF[kind]}/${slug}/`, locale);
}

/** Canonical corpus-work URL for `(kind, slug)` in `locale`. */
export function workUrl(kind: CorpusWorkKind, slug: string, locale: Locale): string {
  return routedUrl(kind, slug, locale);
}

/** Canonical kind-index URL in `locale` (e.g. `/ru/books/` or `/en/poetry/`). */
export function kindIndexUrl(kind: RoutedKind, locale: Locale): string {
  return localizePath(`/${SEGMENT_OF[kind]}/`, locale);
}

/** Canonical static-page URL. */
export function pageUrl(slug: string, locale: Locale): string {
  return localizePath(`/${slug}/`, locale);
}

/** Canonical download endpoint URL for `(kind, slug, format)` in `locale`. */
export function downloadUrl(
  kind: CorpusWorkKind,
  slug: string,
  format: DownloadFormat,
  locale: Locale,
): string {
  const base = localizePath(`/${SEGMENT_OF[kind]}/`, locale);
  return `${base}${slug}.${format}`;
}

/** Home URL for `locale` (e.g. `/ru/`, `/en/`). */
export function homeUrl(locale: Locale): string {
  return `/${LOCALE_META[locale].urlPrefix}/`;
}
