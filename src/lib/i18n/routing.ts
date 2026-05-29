import { SEGMENT_OF, type CorpusWorkKind, type RoutedKind } from "../kinds";
import type { Locale } from "../locales";
import { LOCALE_META } from "./locale-meta";

/** Prefix a root-relative path with the locale segment, except for the default locale. */
export function localizePath(path: string, locale: Locale): string {
  if (!path.startsWith("/")) {
    throw new Error(`localizePath expects an absolute path, got ${JSON.stringify(path)}`);
  }
  const prefix = LOCALE_META[locale].urlPrefix;
  if (!prefix) return path;
  return `/${prefix}${path}`;
}

/** Canonical routed-content URL for `(kind, slug)` in `locale`. Slug must be per-language. */
export function routedUrl(kind: RoutedKind, slug: string, locale: Locale): string {
  return localizePath(`/${SEGMENT_OF[kind]}/${slug}/`, locale);
}

/** Canonical corpus-work URL for `(kind, slug)` in `locale`. */
export function workUrl(kind: CorpusWorkKind, slug: string, locale: Locale): string {
  return routedUrl(kind, slug, locale);
}

/** Canonical kind-index URL in `locale` (e.g. `/books/` or `/en/poetry/`). */
export function kindIndexUrl(kind: RoutedKind, locale: Locale): string {
  return localizePath(`/${SEGMENT_OF[kind]}/`, locale);
}

/** Canonical static-page URL. */
export function pageUrl(slug: string, locale: Locale): string {
  return localizePath(`/${slug}/`, locale);
}

/** Canonical download endpoint URL for `(kind, slug, format)` in `locale`. */
export function downloadUrl(kind: CorpusWorkKind, slug: string, format: string, locale: Locale): string {
  const base = localizePath(`/${SEGMENT_OF[kind]}/`, locale);
  return `${base}${slug}.${format}`;
}

/** Home URL for `locale`. */
export function homeUrl(locale: Locale): string {
  const prefix = LOCALE_META[locale].urlPrefix;
  return prefix ? `/${prefix}/` : "/";
}
