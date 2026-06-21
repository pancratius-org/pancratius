import { defineConfig, fontProviders } from "astro/config";
import { unified } from "@astrojs/markdown-remark";

// Self-hosted web fonts, pinned to the `@fontsource-variable/*` packages in package-lock.json and
// referenced as package imports — Astro subsets and serves them from the origin, so there is no
// Google request at build or runtime and no committed binary. The `standard` files keep the
// optical-size axis (Source Serif's display headings stay refined). `src/styles/tokens.css`
// composes the generated `--font-*` vars into the `--serif` / `--sans` tokens the CSS reads.
const FONT_SUBSETS: Record<string, string> = {
  "latin": "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD",
  "latin-ext": "U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+0304,U+0308,U+0329,U+1D00-1DBF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C0,U+2113,U+2C60-2C7F,U+A720-A7FF",
  "cyrillic": "U+0301,U+0400-045F,U+0490-0491,U+04B0-04B1,U+2116",
  "cyrillic-ext": "U+0460-052F,U+1C80-1C8A,U+20B4,U+2DE0-2DFF,U+A640-A69F,U+FE2E-FE2F",
  // Greek scripture quotes render in body; Hebrew/Arabic/polytonic Greek fall back to system fonts.
  "greek": "U+0370-0377,U+037A-037F,U+0384-038A,U+038C,U+038E-03A1,U+03A3-03FF",
};

type FontVariant = {
  src: [string];
  weight: string;
  style: "normal" | "italic";
  unicodeRange: [string];
};

/** One `@font-face` per subset × style off the pinned Fontsource `standard` (wght+opsz) files. */
const fontsourceVariants = (
  slug: string,
  weight: string,
  styles: ReadonlyArray<"normal" | "italic">,
): [FontVariant, ...FontVariant[]] => {
  const variants = styles.flatMap((style) =>
    Object.entries(FONT_SUBSETS).map(([subset, unicode]): FontVariant => ({
      src: [`@fontsource-variable/${slug}/files/${slug}-${subset}-standard-${style}.woff2`],
      weight,
      style,
      unicodeRange: [unicode],
    })),
  );
  return variants as [FontVariant, ...FontVariant[]];
};

type MutableResolveAlias = {
  alias?: unknown;
};

function dropDeprecatedTsconfigAliasViteEntries(resolve: MutableResolveAlias | undefined): void {
  if (!resolve) return;
  const alias = resolve.alias;
  if (!Array.isArray(alias)) return;
  // Astro's `astro:tsconfig-alias` plugin also resolves tsconfig paths in `resolveId`.
  // Its Vite alias entries still carry deprecated `customResolver` fields under Vite 8,
  // so drop those alias-plugin entries and let Astro's resolver hook do the work.
  resolve.alias = alias.filter(
    (entry) => !(entry !== null && typeof entry === "object" && "customResolver" in entry),
  );
}

function tsconfigAliasWithoutDeprecatedViteEntries() {
  return {
    name: "pancratius:tsconfig-alias-without-deprecated-vite-entries",
    enforce: "post" as const,
    config(config: { resolve?: MutableResolveAlias }) {
      dropDeprecatedTsconfigAliasViteEntries(config.resolve);
    },
    configResolved(config: { resolve: MutableResolveAlias }) {
      dropDeprecatedTsconfigAliasViteEntries(config.resolve);
    },
  };
}

import rehypeAutolinkHeadings from "rehype-autolink-headings";
import rehypeSlug from "rehype-slug";

// The markdown processor is shared across locales, so the heading-anchor
// aria-label can't be a single static string. This pass localizes it per the
// document's frontmatter `lang` (Astro exposes it on the vfile), so an EN page
// gets the English label and a RU page the Russian one.
const HEADING_ANCHOR_ARIA: Record<string, string> = {
  ru: "Постоянная ссылка на этот раздел",
  en: "Permalink to this section",
};
function rehypeLocalizeHeadingAnchors() {
  return (tree: unknown, file: { data?: { astro?: { frontmatter?: { lang?: string } } } }) => {
    const lang = file.data?.astro?.frontmatter?.lang;
    const label = (lang && HEADING_ANCHOR_ARIA[lang]) || HEADING_ANCHOR_ARIA.ru;
    const walk = (node: unknown): void => {
      if (!node || typeof node !== "object") return;
      const n = node as { properties?: Record<string, unknown>; children?: unknown[] };
      const cls = n.properties?.className;
      if (Array.isArray(cls) && cls.includes("heading-anchor")) {
        n.properties!.ariaLabel = label;
      }
      n.children?.forEach(walk);
    };
    walk(tree);
  };
}

// Canonical locale list + default. `./src/lib/locales.ts` is pure TS so the
// i18n config can derive from it. The default locale is the apex `/` redirect
// target; every locale is prefixed (`/ru/`, `/en/`).
import { LOCALES, DEFAULT_LOCALE } from "./src/lib/locales.ts";

// Default `Astro.site` base. Canonical/hreflang/OG/JSON-LD URLs are locale-keyed
// (`src/lib/origins.ts`); the sitemap is emitted per-origin by `build/sitemap.ts`.
const site = process.env.PUBLIC_SITE_URL ?? "https://pancratius.ru";

export default defineConfig({
  site,
  // Canonical URLs are produced by `src/lib/i18n/`: HTML routes end in `/`,
  // file endpoints end in their extension. Astro's global "always" mode also
  // appends `/` to dynamic endpoint params in dev (`foo.md/`), so use
  // "ignore" here and keep the canonical shape in our route helpers.
  trailingSlash: "ignore",
  build: { format: "directory" },
  fonts: [
    {
      provider: fontProviders.local(),
      name: "Source Serif 4",
      cssVariable: "--font-serif",
      fallbacks: ["PT Serif", "Georgia", "serif"],
      options: { variants: fontsourceVariants("source-serif-4", "300 700", ["normal", "italic"]) },
    },
    {
      provider: fontProviders.local(),
      name: "Inter",
      cssVariable: "--font-sans",
      fallbacks: ["system-ui", "sans-serif"],
      options: { variants: fontsourceVariants("inter", "400 600", ["normal"]) },
    },
  ],
  i18n: {
    defaultLocale: DEFAULT_LOCALE,
    locales: [...LOCALES],
    routing: {
      // Every locale is prefixed (`/ru/`, `/en/`). The apex `/` is owned by
      // `src/pages/index.astro` (a 301 to the default-locale home), which `.org`
      // overrides to `/en/` host-side — see docs/architecture.md (Apex redirect).
      prefixDefaultLocale: true,
    },
  },
  vite: {
    plugins: [tsconfigAliasWithoutDeprecatedViteEntries()],
  },
  markdown: {
    shikiConfig: {
      theme: "github-dark",
      wrap: true,
    },
    processor: unified({
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
        // Runs after the anchors are appended; rewrites the aria-label to the
        // page's own language (the static label above is the RU default).
        rehypeLocalizeHeadingAnchors,
      ],
    }),
  },
});
