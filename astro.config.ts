import { existsSync, readFileSync } from "node:fs";
import { resolve as resolvePath } from "node:path";

import { defineConfig } from "astro/config";
import sitemap from "@astrojs/sitemap";

// Deploy target selection. Canonical home is the primary static hosting
// deploy; the GitHub Pages mirror lives at https://<owner>.github.io/<repo>/
// and needs a base prefix so asset URLs resolve correctly. CI sets
// PUBLIC_DEPLOY_TARGET=github-pages on the mirror workflow;
// GITHUB_REPOSITORY is "<owner>/<repo>" inside any GitHub Action.
const deployTarget = process.env.PUBLIC_DEPLOY_TARGET ?? "primary";
const ghRepo = process.env.GITHUB_REPOSITORY ?? "";
const [ghOwner, ghRepoName] = ghRepo.split("/");
const isGhPages = deployTarget === "github-pages";
const primarySite = process.env.PUBLIC_SITE_URL ?? "https://pancratius.ru";

const site = isGhPages && ghOwner
  ? `https://${ghOwner}.github.io`
  : primarySite;
const base = isGhPages && ghRepoName ? `/${ghRepoName}/` : undefined;

// ──────────────────────────────────────────────────────────────────
// Sitemap hreflang pairing.
//
// `@astrojs/sitemap`'s built-in i18n alternate generation assumes parallel
// slugs across locales; Pancratius's slugs differ per language, so we attach
// `links` per route using a precomputed manifest. The manifest is built by
// `scripts/build_slug_map.py` and lives at `data/slug-map.json` (gitignored,
// regenerated before every build/dev/check).
// ──────────────────────────────────────────────────────────────────

type SlugMap = {
  works: {
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
const worksByLangSlug = new Map<string, SlugMap["works"][number]>();
const pagesByLangSlug = new Map<string, SlugMap["pages"][number]>();
if (slugMap) {
  for (const w of slugMap.works) {
    for (const lang of Object.keys(w.languages)) {
      worksByLangSlug.set(`${w.kind}:${lang}:${w.languages[lang].slug}`, w);
    }
  }
  for (const p of slugMap.pages) {
    for (const lang of Object.keys(p.languages)) {
      pagesByLangSlug.set(`${lang}:${p.slug}`, p);
    }
  }
}

const SEGMENT_TO_KIND: Record<string, "book" | "poem" | "project"> = {
  books:    "book",
  poetry:   "poem",
  projects: "project",
};

const WORK_RE = /^(?:\/(en))?\/(books|poetry|projects)\/([^/]+)\/?$/;
const PAGE_RE = /^(?:\/(en))?\/([^/]+)\/?$/;

// Structural routes that exist in every locale and are not in the `pages`
// collection (no authored Markdown). The sitemap resolver needs to pair them
// up the same way it pairs work URLs; we keep the list narrow so it's easy
// to reason about, not a catch-all.
const STRUCTURAL_BOTH_LOCALES: Record<string, { ru: string; en: string }> = {
  conceptosphere: { ru: "/conceptosphere/", en: "/en/conceptosphere/" },
};

function withXDefault(links: { lang: string; url: string }[]): { lang: string; url: string }[] {
  // Match page-level <head> behaviour: append x-default → RU canonical.
  const ru = links.find(l => l.lang === "ru");
  if (!ru) return links;
  return [...links, { lang: "x-default", url: ru.url }];
}

function alternatesFromUrl(itemUrlString: string): { lang: string; url: string }[] | null {
  let url: URL;
  try { url = new URL(itemUrlString); } catch { return null; }
  let pathname = url.pathname;
  if (base && pathname.startsWith(base)) {
    pathname = "/" + pathname.slice(base.length).replace(/^\/+/, "");
  }

  const mWork = pathname.match(WORK_RE);
  if (mWork) {
    const lang = mWork[1] ?? "ru";
    const kind = SEGMENT_TO_KIND[mWork[2]];
    const slug = mWork[3];
    const w = worksByLangSlug.get(`${kind}:${lang}:${slug}`);
    if (!w) return null;
    const links = Object.entries(w.languages).map(([l, info]) => {
      const path = base ? base.replace(/\/$/, "") + info.url : info.url;
      return { lang: l, url: new URL(path, url.origin).toString() };
    });
    return withXDefault(links);
  }

  const mPage = pathname.match(PAGE_RE);
  if (mPage && SEGMENT_TO_KIND[mPage[2]] === undefined) {
    const slug = mPage[2];
    // Structural routes (e.g. /conceptosphere/) live in both locales but
    // aren't in the `pages` collection. Resolve their alternates from the
    // structural table before falling back to the pages lookup.
    const structural = STRUCTURAL_BOTH_LOCALES[slug];
    if (structural) {
      const links = (["ru", "en"] as const).map((l) => {
        const urlPath = structural[l];
        const path = base ? base.replace(/\/$/, "") + urlPath : urlPath;
        return { lang: l, url: new URL(path, url.origin).toString() };
      });
      return withXDefault(links);
    }
    const lang = mPage[1] ?? "ru";
    const p = pagesByLangSlug.get(`${lang}:${slug}`);
    if (!p) return null;
    const links = Object.entries(p.languages).map(([l, urlPath]) => {
      const path = base ? base.replace(/\/$/, "") + urlPath : urlPath;
      return { lang: l, url: new URL(path, url.origin).toString() };
    });
    return withXDefault(links);
  }

  return null;
}

// ──────────────────────────────────────────────────────────────────
// Body-image hardening.
//
// Converter-emitted markdown bodies contain raw `<img>` tags (pandoc emits
// HTML for images inside tables). These tags ship without `alt`,
// `loading="lazy"`, or `decoding="async"`, which costs us a11y and
// first-paint quality. A small rehype plugin fills in safe defaults; it
// never overwrites attributes the converter set.
// ──────────────────────────────────────────────────────────────────

type HastNode = {
  type: string;
  tagName?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
};

function rehypeBodyImages() {
  return (tree: HastNode) => {
    const visit = (node: HastNode) => {
      if (node.type === "element" && node.tagName === "img") {
        const props = node.properties ?? (node.properties = {});
        if (props.alt === undefined || props.alt === null) props.alt = "";
        if (!props.loading) props.loading = "lazy";
        if (!props.decoding) props.decoding = "async";
      }
      if (node.children) for (const c of node.children) visit(c);
    };
    visit(tree);
  };
}

export default defineConfig({
  site,
  ...(base ? { base } : {}),
  trailingSlash: "always",
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
    defaultLocale: "ru",
    locales: ["ru", "en"],
    routing: {
      prefixDefaultLocale: false,
    },
  },
  markdown: {
    rehypePlugins: [rehypeBodyImages],
    shikiConfig: {
      theme: "github-dark",
      wrap: true,
    },
  },
});
