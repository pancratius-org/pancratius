import { defineConfig, fontProviders } from "astro/config";
import { satteri, satteriHeadingIdsPlugin } from "@astrojs/markdown-satteri";
import type { HastPluginDefinition, HastPluginInput } from "satteri";
import type { Element } from "hast";

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

// Heading anchors aren't native to Sätteri: `satteriHeadingIdsPlugin` assigns
// the `id`, this appends the citable "#" link to each h2/h3/h4. The processor is
// shared across locales, so the aria-label is localized per the document's
// frontmatter `lang` (an EN page gets the English label, RU the Russian, anything
// else the RU default). The anchor carries no text — the visible "#" is drawn by
// CSS (`.heading-anchor::after`, reading/elements.css), which keeps a literal "#"
// out of ToC text and search snippets.
const HEADING_ANCHOR_ARIA: Record<string, string> = {
  ru: "Постоянная ссылка на этот раздел",
  en: "Permalink to this section",
};
function headingAnchorsPlugin(): HastPluginDefinition {
  return {
    name: "heading-anchors",
    element: {
      filter: ["h2", "h3", "h4"],
      visit(node, ctx) {
        const id = node.properties?.id;
        if (typeof id !== "string") return;
        // `frontmatter` is typed `Record<string, any>`, so narrow `lang` to a
        // real string before the lookup rather than indexing on `any`.
        const lang = ctx.data.astro?.frontmatter?.lang;
        const ariaLabel =
          (typeof lang === "string" ? HEADING_ANCHOR_ARIA[lang] : undefined) ??
          HEADING_ANCHOR_ARIA.ru;
        const anchor: Element = {
          type: "element",
          tagName: "a",
          properties: { href: `#${id}`, className: ["heading-anchor"], ariaLabel },
          children: [],
        };
        ctx.appendChild(node, anchor);
      },
    },
  };
}

// `satteriHeadingIdsPlugin` runs first so the `id` exists when `headingAnchorsPlugin`
// reads it: Sätteri's own id pass runs last, after user plugins, and re-runs
// idempotently over existing ids — so this user pass is required, not redundant.
// Both are passed as factories, not instances: Sätteri re-instantiates each per
// document, so the slugger resets per page. A shared instance would carry slug
// counts across the whole build and append spurious suffixes to headings that
// repeat between books ("Prologue" → "prologue-1"). `satteri()` types `hastPlugins`
// as ready definitions, but the engine per-document-instantiates the
// `HastPluginInput` factory form it forwards to — hence the assertion at the call site.
const markdownHastPlugins: HastPluginInput[] = [satteriHeadingIdsPlugin, headingAnchorsPlugin];

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
    // Astro 7's native Rust Markdown engine (replaces the remark/rehype pipeline).
    // GFM, smart punctuation, and footnotes are on by default; raw converter HTML
    // passes through untouched. Math only RECOGNIZES `$$ … $$` (rendering LaTeX is a
    // separate KaTeX/MathML step, deferred until the corpus has math);
    // `singleDollarTextMath: false` keeps a lone `$` literal so prose currency
    // ("$160 million") isn't parsed as math.
    processor: satteri({
      features: { math: { singleDollarTextMath: false } },
      hastPlugins: markdownHastPlugins as HastPluginDefinition[],
    }),
  },
});
