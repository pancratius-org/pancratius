import { existsSync, readFileSync } from "node:fs";
import { resolve as resolvePath } from "node:path";

import { defineConfig } from "astro/config";
import sitemap from "@astrojs/sitemap";
import rehypeAutolinkHeadings from "rehype-autolink-headings";
import rehypeSlug from "rehype-slug";

// Canonical segment → kind map. `./src/lib/kinds.ts` is pure TS (no
// `astro:content` import) precisely so this config can import it.
import { KIND_OF_SEGMENT, SEGMENT_OF } from "./src/lib/kinds.ts";

// Canonical locale list + default. `./src/lib/locales.ts` is pure TS (same
// reason as kinds) so the i18n config and the URL grammar below derive from it.
import { LOCALES, DEFAULT_LOCALE } from "./src/lib/locales.ts";

// Site origin baked into canonical URLs, sitemap, OpenGraph, JSON-LD.
const site = process.env.PUBLIC_SITE_URL ?? "https://pancratius.ru";

// ──────────────────────────────────────────────────────────────────
// Sitemap hreflang pairing.
//
// `@astrojs/sitemap`'s built-in i18n alternate generation assumes parallel
// slugs across locales; Pancratius's slugs differ per language, so we attach
// `links` per route using a precomputed manifest. The manifest is built by
// `build/slug-map.ts` and lives at `data/slug-map.json` (gitignored,
// regenerated before every build/dev/check).
// ──────────────────────────────────────────────────────────────────

type SlugMap = {
  entries: {
    kind:   "book" | "poem" | "project";
    number: number;
    languages: Record<string, { slug: string; url: string }>;
  }[];
  pages: {
    slug: string;
    languages: Record<string, string>;
  }[];
};

const slugMapPath = resolvePath(import.meta.dirname, "data", "slug-map.json");
const slugMap: SlugMap | null = existsSync(slugMapPath)
  ? (JSON.parse(readFileSync(slugMapPath, "utf-8")) as SlugMap)
  : null;

// Build a lookup keyed by `(kind, lang, slug)` so we can derive alternates
// from any work URL the sitemap visits.
const entriesByLangSlug = new Map<string, SlugMap["entries"][number]>();
const pagesByLangSlug = new Map<string, SlugMap["pages"][number]>();
if (slugMap) {
  for (const entry of slugMap.entries) {
    for (const lang of Object.keys(entry.languages)) {
      entriesByLangSlug.set(`${entry.kind}:${lang}:${entry.languages[lang].slug}`, entry);
    }
  }
  for (const p of slugMap.pages) {
    for (const lang of Object.keys(p.languages)) {
      pagesByLangSlug.set(`${lang}:${p.slug}`, p);
    }
  }
}

// URL grammar derived from the SSOTs. The locale prefix alternation lists the
// non-default locales (the default locale is unprefixed); the work-segment
// alternation lists the structural-noun segments from `SEGMENT_OF`. Values are
// regex-escaped so an exotic locale/segment token can't break the pattern.
const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const NON_DEFAULT_LOCALES = LOCALES.filter((l) => l !== DEFAULT_LOCALE);
const LOCALE_PREFIX = NON_DEFAULT_LOCALES.map(escapeRe).join("|");        // e.g. "en"
const WORK_SEGMENTS = Object.values(SEGMENT_OF).map(escapeRe).join("|");  // e.g. "books|poetry|projects"

const WORK_RE = new RegExp(`^(?:\\/(${LOCALE_PREFIX}))?\\/(${WORK_SEGMENTS})\\/([^/]+)\\/?$`);
const PAGE_RE = new RegExp(`^(?:\\/(${LOCALE_PREFIX}))?\\/([^/]+)\\/?$`);

// Prefix a default-locale (root-relative, leading-slash) path with a locale
// segment, except for the default locale. Mirror of `localizePath` in
// `src/lib/i18n.ts`; kept local so this config stays decoupled from the chrome
// registry while still deriving prefixes from the locale SSOT. NOTE: this uses
// the locale code directly as the URL prefix. It matches `localizePath` only
// while every non-default locale's `LOCALE_META.urlPrefix === its code`; if a
// future locale sets a divergent `urlPrefix`, teach this mirror to consult it.
function localizeStructuralPath(defaultPath: string, locale: string): string {
  if (locale === DEFAULT_LOCALE) return defaultPath;
  return `/${locale}${defaultPath}`;
}

// Structural routes that exist in every locale and are not in the `pages`
// collection (no authored Markdown). Keep this list concrete: a route belongs
// here only when its localized variants are real pages with matching intent.
// Stored as default-locale root-relative paths; the per-locale URLs are derived
// from the locale list so a third locale needs no edits here.
const STRUCTURAL_PATHS: readonly string[] = [
  "/",
  "/books/",
  "/poetry/",
  "/projects/",
  "/conceptosphere/",
  "/search/",
];

// Map any localized variant of a structural path back to its canonical
// (default-locale) form, so we can regenerate the full alternate set from it.
const structuralByPath = new Map<string, string>();
for (const defaultPath of STRUCTURAL_PATHS) {
  for (const loc of LOCALES) {
    structuralByPath.set(localizeStructuralPath(defaultPath, loc), defaultPath);
  }
}

function withXDefault(links: { lang: string; url: string }[]): { lang: string; url: string }[] {
  // Match page-level <head> behaviour: append x-default → default-locale canonical.
  const canonical = links.find(l => l.lang === DEFAULT_LOCALE);
  if (!canonical) return links;
  return [...links, { lang: "x-default", url: canonical.url }];
}

function alternatesFromUrl(itemUrlString: string): { lang: string; url: string }[] | null {
  let url: URL;
  try { url = new URL(itemUrlString); } catch { return null; }
  const pathname = url.pathname;

  const structuralDefaultPath = structuralByPath.get(pathname);
  if (structuralDefaultPath) {
    const links = LOCALES.map((l) => {
      const urlPath = localizeStructuralPath(structuralDefaultPath, l);
      return { lang: l, url: new URL(urlPath, url.origin).toString() };
    });
    return withXDefault(links);
  }

  const mWork = pathname.match(WORK_RE);
  if (mWork) {
    const lang = mWork[1] ?? DEFAULT_LOCALE;
    const kind = KIND_OF_SEGMENT[mWork[2]];
    const slug = mWork[3];
    const w = entriesByLangSlug.get(`${kind}:${lang}:${slug}`);
    if (!w) return null;
    const links = Object.entries(w.languages).map(([l, info]) => {
      return { lang: l, url: new URL(info.url, url.origin).toString() };
    });
    return withXDefault(links);
  }

  const mPage = pathname.match(PAGE_RE);
  if (mPage && KIND_OF_SEGMENT[mPage[2]] === undefined) {
    const slug = mPage[2];
    const lang = mPage[1] ?? DEFAULT_LOCALE;
    const p = pagesByLangSlug.get(`${lang}:${slug}`);
    if (!p) return null;
    const links = Object.entries(p.languages).map(([l, urlPath]) => {
      return { lang: l, url: new URL(urlPath, url.origin).toString() };
    });
    return withXDefault(links);
  }

  return null;
}

export default defineConfig({
  site,
  // Canonical URLs are produced by `src/lib/i18n.ts`: HTML routes end in `/`,
  // file endpoints end in their extension. Astro's global "always" mode also
  // appends `/` to dynamic endpoint params in dev (`foo.md/`), so use
  // "ignore" here and keep the canonical shape in our route helpers.
  trailingSlash: "ignore",
  build: { format: "directory" },
  integrations: [
    sitemap({
      serialize(item) {
        const links = alternatesFromUrl(item.url);
        if (links && links.filter(l => l.lang !== "x-default").length > 1) {
          return { ...item, links };
        }
        return item;
      },
    }),
  ],
  i18n: {
    defaultLocale: DEFAULT_LOCALE,
    locales: [...LOCALES],
    routing: {
      prefixDefaultLocale: false,
    },
  },
  markdown: {
    shikiConfig: {
      theme: "github-dark",
      wrap: true,
    },
    rehypePlugins: [
      // `rehype-slug` must run before `rehype-autolink-headings`, which
      // only adds anchors to headings that already have an `id`. Astro's
      // own slugger runs on its own pass for the `headings` collection
      // and isn't visible to user plugins in the right order, so we add
      // an explicit slug pass here. Result: every prose h2/h3/h4 gets a
      // citable id and an anchor.
      rehypeSlug,
      [rehypeAutolinkHeadings, {
        behavior: "append",
        test: ["h2", "h3", "h4"],
        properties: {
          className: ["heading-anchor"],
          ariaLabel: "Постоянная ссылка на этот раздел",
        },
        // No text content — the visible "#" is drawn by CSS via
        // `::after`. That keeps the literal "#" out of plain-text
        // extractors (ToC heading text, search snippets) which would
        // otherwise pick up "Heading text#".
        content: [],
      }],
    ],
  },
});
