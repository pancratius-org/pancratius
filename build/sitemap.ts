#!/usr/bin/env node
// Per-origin sitemap emitter.
//
// Both domains are regional mirrors of one `dist/`, but a sitemap may only list
// URLs on its own host, so each origin gets its own file: `sitemap-ru.xml`
// (pancratius.ru) and `sitemap-org.xml` (pancratius.org). Each lists its
// locale's canonical URLs and carries reciprocal cross-origin `<xhtml:link>`
// hreflang alternates (ru↔.ru, en↔.org) plus `x-default`.
//
// Pages whose locale versions share a path (indexes, home, project sub-pages)
// resolve alternates by swapping the locale prefix; works and static pages have
// per-language slugs, so their alternates come from `data/slug-map.json`.
// Error pages and the apex redirect stub are not canonical and are omitted.

import { existsSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { KIND_OF_SEGMENT, isRoutedSegment, type RoutedKind } from "../src/lib/kinds.ts";
import { LOCALES, DEFAULT_LOCALE, type Locale } from "../src/lib/locales.ts";
import { LOCALE_META, localeFromPrefix } from "../src/lib/i18n/locale-meta.ts";
import { originFor } from "../src/lib/origins.ts";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const DIST = join(REPO_ROOT, "dist");
const SLUG_MAP = join(REPO_ROOT, "data", "slug-map.json");

type LangUrl = { slug: string; url: string; origin: string };
type SlugMap = {
  entries: { kind: RoutedKind; number: number; languages: Partial<Record<Locale, LangUrl>> }[];
  pages: { slug: string; languages: Partial<Record<Locale, Omit<LangUrl, "slug">>> }[];
};

/** A canonical page and its authored-locale alternates, keyed by locale. */
type Alternate = { locale: Locale; href: string };

function loadSlugMap(): SlugMap {
  if (!existsSync(SLUG_MAP)) throw new Error(`slug map missing: ${relative(REPO_ROOT, SLUG_MAP)}`);
  return JSON.parse(readFileSync(SLUG_MAP, "utf-8")) as SlugMap;
}

/** Every emitted HTML page as a leading-slash, directory-style path (`/ru/books/x/`). */
function emittedPages(): string[] {
  const pages: string[] = [];
  const walk = (dir: string): void => {
    for (const name of readdirSync(dir).sort()) {
      const abs = join(dir, name);
      if (statSync(abs).isDirectory()) {
        walk(abs);
      } else if (name === "index.html") {
        const rel = relative(DIST, dir).split(/[/\\]/).join("/");
        pages.push(rel === "" ? "/" : `/${rel}/`);
      }
    }
  };
  walk(DIST);
  return pages;
}

/** Strip the leading locale prefix, leaving the locale-neutral remainder (`/books/x/`). */
function unprefixed(path: string, locale: Locale): string {
  return path.slice(`/${LOCALE_META[locale].urlPrefix}`.length);
}

function absolute(locale: Locale, path: string): string {
  return new URL(path, originFor(locale)).toString();
}

/** Alternates for a work/static-page path via the per-language slug map, or null if not one. */
function slugMapAlternates(path: string, locale: Locale, map: SlugMap): Alternate[] | null {
  const rest = unprefixed(path, locale);
  const work = rest.match(/^\/([^/]+)\/([^/]+)\/$/);
  if (work) {
    const [, segment, slug] = work;
    const kind = segment === undefined || !isRoutedSegment(segment)
      ? undefined
      : KIND_OF_SEGMENT[segment];
    if (kind === undefined || slug === undefined) return null;
    const entry = map.entries.find((e) => e.kind === kind && e.languages[locale]?.slug === slug);
    if (!entry) return null;
    return localeOrder(entry.languages);
  }
  const page = rest.match(/^\/([^/]+)\/$/);
  if (page) {
    const slug = page[1];
    const entry = map.pages.find((p) => p.slug === slug && p.languages[locale]);
    if (!entry) return null;
    return localeOrder(entry.languages);
  }
  return null;
}

function localeOrder(languages: Partial<Record<Locale, { url: string; origin: string }>>): Alternate[] {
  const out: Alternate[] = [];
  for (const loc of LOCALES) {
    const lang = languages[loc];
    if (lang) out.push({ locale: loc, href: new URL(lang.url, lang.origin).toString() });
  }
  return out;
}

/** Alternates for a shared-path page (index, home, sub-page): the prefix-swapped siblings that exist. */
function parallelAlternates(path: string, locale: Locale, emitted: ReadonlySet<string>): Alternate[] {
  const rest = unprefixed(path, locale);
  const out: Alternate[] = [];
  for (const loc of LOCALES) {
    const sibling = `/${LOCALE_META[loc].urlPrefix}${rest}`;
    if (emitted.has(sibling)) out.push({ locale: loc, href: absolute(loc, sibling) });
  }
  return out;
}

/** x-default → EN when an English version is authored, else the default-locale (RU) version. */
function xDefault(alternates: Alternate[]): Alternate | null {
  return alternates.find((a) => a.locale === "en") ?? alternates.find((a) => a.locale === DEFAULT_LOCALE) ?? null;
}

const SITEMAP_FILE: Record<Locale, string> = {
  ru: "sitemap-ru.xml",
  en: "sitemap-org.xml",
};

function isSitemapPage(path: string, locale: Locale): boolean {
  // Skip error pages; the apex redirect stub has no locale and is skipped upstream.
  return !path.endsWith("/404/") && unprefixed(path, locale) !== "/404/";
}

type UrlNode = { loc: string; alternates: Alternate[] };

function renderSitemap(nodes: UrlNode[]): string {
  const xmlns = `xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml"`;
  const body = nodes
    .map((node) => {
      const links = node.alternates
        .map((a) => `    <xhtml:link rel="alternate" hreflang="${a.locale}" href="${a.href}" />`)
        .concat(
          (() => {
            const x = xDefault(node.alternates);
            return x ? [`    <xhtml:link rel="alternate" hreflang="x-default" href="${x.href}" />`] : [];
          })(),
        )
        .join("\n");
      return `  <url>\n    <loc>${node.loc}</loc>${links ? `\n${links}` : ""}\n  </url>`;
    })
    .join("\n");
  // Crawlers ignore the stylesheet PI; browsers use it to render sitemap.xsl.
  const stylesheet = `<?xml-stylesheet type="text/xsl" href="/sitemap.xsl"?>`;
  return `<?xml version="1.0" encoding="UTF-8"?>\n${stylesheet}\n<urlset ${xmlns}>\n${body}\n</urlset>\n`;
}

function main(): number {
  if (!existsSync(DIST)) throw new Error("dist/ missing — run `astro build` first");
  const map = loadSlugMap();
  const pages = emittedPages();
  const emitted = new Set(pages);

  const byLocale: Record<Locale, UrlNode[]> = { ru: [], en: [] };
  for (const path of pages) {
    const locale = localeFromPrefix(path);
    if (!locale || !isSitemapPage(path, locale)) continue;
    const alternates =
      slugMapAlternates(path, locale, map) ?? parallelAlternates(path, locale, emitted);
    // Only emit reciprocal hreflang when a real sibling exists; a lone page lists just itself.
    const links = alternates.length > 1 ? alternates : [];
    byLocale[locale].push({ loc: absolute(locale, path), alternates: links });
  }

  for (const locale of LOCALES) {
    const file = join(DIST, SITEMAP_FILE[locale]);
    writeFileSync(file, renderSitemap(byLocale[locale]), "utf-8");
    console.log(`sitemap: ${relative(REPO_ROOT, file)}  urls=${byLocale[locale].length}`);
  }
  return 0;
}

process.exit(main());
