// Static-page loader for the `pages` collection.
//
// `pages` lives outside the work `(kind, number)` model and serves
// /about/, /mission/, /svetozar/, /license/, /support/, /downloads/. Each
// page has at most one entry per locale; missing locales fall back to nothing
// (the route resolves to a 404 in that locale).

import { getCollection, type CollectionEntry } from "astro:content";

import type { Locale } from "./i18n";
import { RESERVED_PAGE_SLUGS } from "./i18n";

export type PageEntry = CollectionEntry<"pages">;

/**
 * Page slugs whose URL is owned by a hand-written route, not by the dynamic
 * `/[slug].astro` renderer. The prose still lives in the pages collection so
 * authors edit Markdown, but the dedicated route renders extra structure
 * around it (a download table, etc.). `getStaticPagesForSlugRoute` filters
 * these out so Astro doesn't warn about a route conflict.
 */
export const PAGES_WITH_DEDICATED_ROUTE: ReadonlySet<string> = new Set([
  "downloads",
]);

let _cache: PageEntry[] | null = null;

async function loadPages(): Promise<PageEntry[]> {
  if (_cache) return _cache;
  const entries = await getCollection("pages");
  for (const entry of entries) {
    if (RESERVED_PAGE_SLUGS.has(entry.data.slug)) {
      throw new Error(
        `pages: slug ${JSON.stringify(entry.data.slug)} shadows a structural route. ` +
        `Pick a different slug or whitelist this collision explicitly.`,
      );
    }
  }
  _cache = entries;
  return entries;
}

export async function getPage(slug: string, locale: Locale): Promise<PageEntry | null> {
  const all = await loadPages();
  return all.find(e => e.data.slug === slug && e.data.lang === locale) ?? null;
}

/**
 * Pages eligible for `/[slug].astro`'s `getStaticPaths`. Excludes any slug
 * served by a hand-written route, avoiding the route-conflict warning.
 */
export async function getPagesForSlugRoute(locale: Locale): Promise<PageEntry[]> {
  const all = await loadPages();
  return all.filter(e =>
    e.data.lang === locale && !PAGES_WITH_DEDICATED_ROUTE.has(e.data.slug),
  );
}

/** Counterpart in the other language, if authored. */
export async function alternateLanguagePage(
  page: PageEntry,
  target: Locale,
): Promise<PageEntry | null> {
  if (page.data.lang === target) return page;
  return getPage(page.data.slug, target);
}
