// Static-page loader for the `pages` collection.
//
// `pages` lives outside the work `(kind, number)` model and serves
// /about/, /mission/, /svetozar/, /license/, /support/, /downloads/. Each
// page is an *individual* with its own dedicated route (no dynamic dispatcher);
// the prose still lives in the collection so authors edit one Markdown file per
// locale. Each page has at most one entry per locale; missing locales resolve
// to a 404 in that locale (the route 404s/skips when `getPage` returns null).

import { getCollection, type CollectionEntry } from "astro:content";

import type { Locale } from "./i18n";
import { RESERVED_PAGE_SLUGS } from "./i18n";

export type PageEntry = CollectionEntry<"pages">;

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

/** Counterpart in the other language, if authored. */
export async function alternateLanguagePage(
  page: PageEntry,
  target: Locale,
): Promise<PageEntry | null> {
  if (page.data.lang === target) return page;
  return getPage(page.data.slug, target);
}
