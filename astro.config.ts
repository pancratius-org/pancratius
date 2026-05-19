import { existsSync, readFileSync } from "node:fs";
import { resolve as resolvePath } from "node:path";

import { defineConfig } from "astro/config";
import sitemap from "@astrojs/sitemap";
import rehypeAutolinkHeadings from "rehype-autolink-headings";
import rehypeSlug from "rehype-slug";

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
    // Raw `<img>` tags (the only form the corpus emits) bypass the rehype
    // pipeline because Astro's default markdown processor passes them through
    // as `raw` nodes, not `element` nodes. `scripts/build_copy_body_images.py`
    // adds alt/loading/decoding post-build instead.
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
