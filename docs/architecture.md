# Pancratius Architecture

Production-site architecture. This file names the stable boundaries. Commands
live in [`tooling.md`](./tooling.md); CI details live in `.github/workflows/`.

## Boundaries

These rules decide where a change belongs.

- **Content kinds.** Books and poems are a *population* (one shape, paired by
  `(kind, number)`, full downloads); pages are *individuals* (dedicated routes);
  projects are *themed mini-sites*, not works. → [`content-model.md`](./content-model.md).
- **Command ownership.** `pancratius` changes the library. `npm` builds, checks,
  previews, audits, and deploys the site. → [`tooling.md`](./tooling.md).
- **Mechanical vs editorial.** The CLI may convert, scaffold, render, optimize,
  and regenerate data. It must not decide titles, descriptions, project shape,
  theological register, or publication judgment. → [`tooling.md`](./tooling.md).
- **Local vs CI.** Import DOCX → source and render release artifacts locally; CI
  only builds and publishes — it does not import works, render release artifacts,
  optimize DOCX, or regenerate embeddings. **Exception:** light external-metadata
  ingestion (e.g. `pancratius video sync` via `.github/workflows/video-sync.yml`)
  runs in CI because it is additive, idempotent, and stays away from the heavy
  paths above.
- **Fallback vs route existence.** Display data may fall back to the default
  locale; a route, download, or feed exists only where that locale was authored.
  → [`i18n-routing.md`](./i18n-routing.md).

## Flow

```txt
local library work
  source DOCX / authored edits
      -> pancratius
      -> src/content + committed data/products

site work
  committed source
      -> npm build/check/audit
      -> dist
      -> production hosts
```

The first flow creates or changes the library. The second publishes what is
already committed. Their meeting point is committed source.

## Stack

- **Framework**: Astro 6+. No additional UI framework — no React, no Vue, no Svelte, no Solid, no Tailwind. Vanilla CSS is scoped through Astro components.
- **Language**: TypeScript 6+, strict mode, everywhere in production source. No
  handwritten production JavaScript (`.js` / `.mjs` / `.cjs`) in source trees.
  Non-production JavaScript belongs outside the tsconfig-included trees.
- **Runtime**: Node 24.
- **Python tooling**: Python 3.13+ with type hints. Run through `uv`; dependencies
  are locked in `pyproject.toml` / `uv.lock`. No `pip`, `conda`, or
  `requirements.txt`.
- **Conceptosphere viz libs**: Sigma 3, `graphology`, and
  `graphology-layout-forceatlas2`, bundled through npm.
- **Search**: Pagefind (static; ships with the build).
- **Library-management tools**: pandoc, typst, pymorphy3, Qwen3-Embedding via
  MLX. These are local/admin tools, not deploy tools.
- **License**: corpus content is CC0 1.0 Universal; site/tooling software is MIT.

## Site Shape

- **Static output.** Astro emits files; the host serves files. No SSR, runtime
  backend, or API surface.
- **CI publishes committed source.** CI validates content, builds Astro, runs
  Pagefind/sitemap generation, checks referenced artifacts, and deploys `dist/`.
  It does not optimize DOCX, render PDF/EPUB, or regenerate embeddings.
- **One URL = one resource.** Language, content, downloads, and alternate links
  follow from the URL. There is no separate UI-language state. See
  [`i18n-routing.md`](./i18n-routing.md).
- **Canonical URLs.** HTML ends in `/`; file downloads end in the extension.
  Shared URL helpers own this shape. Astro uses `trailingSlash: "ignore"` so
  dynamic file endpoints such as `/ru/books/{slug}.md` are not rewritten in dev.
- **Dual origin, one build.** Two domains are regional mirrors of the identical
  full bilingual `dist/`: `pancratius.ru` (ccTLD, Russian audience) and
  `pancratius.org` (gTLD, international). Canonical/hreflang/OG/JSON-LD origin is
  a function of the resource's **locale** (`src/lib/origins.ts`), never of the
  serving host, so the artifact stays byte-identical across both. Language
  switching is same-origin; the cross-origin hreflang map is the SEO axis.
- **Apex redirect is host-decided.** The build bakes the apex `/ → /ru/`
  as a meta-refresh (static output → no real 301); only `.org` upgrades it to a
  true 301 → `/en/` at the edge. The apex is the only host-specific behavior; `dist/` carries no
  host overlay.
- **Deploy targets.** The same `dist/` ships to a plain SSH/rsync host (`.ru`)
  and to Cloudflare Pages (`.org`). The rsync host sets `Content-Type` from file
  extension; Astro `Response` headers do not carry over to deployed static files.

## Styling

- **Styling lives in `src/styles/`** (design tokens, typography roles, layout
  primitives) plus component-scoped `<style>` blocks. `global.css` imports the
  shared layers; components own their local styling.
- **Typography is two tiers.** Shared *roles* live in `src/styles/typography.css`
  — named custom properties and classes for treatments that recur across
  unrelated surfaces (index masthead title, kicker/eyebrow, footer note, work
  card sub-roles, static and utility titles). Each role is documented where it is
  defined; that file is the registry.
- **Local registers own their own type.** Reading, projects, conceptosphere,
  home, almanac, and terminal define typography locally and are not folded into
  shared roles. A repeated *number* is not a role; a role names a shared
  *meaning*. Reference a role instead of copying its literal, and do not promote a
  local value just because the number recurs.

## Content

- **Source of truth lives in `src/content/`** as Markdown plus sibling source/release artifacts (DOCX, PDF, EPUB, covers, images). See [`content-model.md`](./content-model.md).
- **Build-pipeline data lives in `data/`** and is **not** web-public. Astro's static build does not ship `data/`. Public payloads (graph JSON, etc.) are emitted or copied into `public/data/` so they appear in `dist/`.
- **Embedding caches and `conceptosphere-embed.json`** are build-time inputs only. Never publish them.

## Images and asset route

Authored image sources live inside the work bundle:

- `cover.<lang>.<ext>` for canonical covers.
- `images/*` for body illustrations shared across translations.
- `images/<lang>/*` for language-specific body illustrations.

Only converter-imported inline DOCX images may use short hashed filenames such
as `images/649a499a5bdb.jpg`. Treat those names as stable imported asset IDs
after first import, not as a rule that the filename must always match current
bytes. Covers remain human-named (`cover.ru.jpg`, `cover.en.jpg`), and
bibliography/reference thumbnails are lifted into structured data instead of
kept as Markdown images.

The build pipeline may hash, optimize, deduplicate, and emit public renditions
into `dist/`, but `public/media/` is not the author-facing source of truth. A
book should be addable from one work folder.

Four image roles cover the corpus:

- **canonical work cover** — one per (work, lang), referenced from frontmatter
  `cover`.
- **body illustration** — author-supplied inline image, kept in the markdown
  body as a relative work-bundle asset.
- **bibliography thumbnail** — image embedded inside a source DOCX table listing
  other works with marketplace URLs. Lifted into `bibliography.yaml`; the
  markdown body never carries the table or its thumbnails.
- **decorative junk** — anchor spans, page-strip artefacts. Dropped during
  conversion.

The mechanism can be Astro's image pipeline, a custom copy/optimize step, or
both. The invariant is source assets with the work; generated public assets in
build output.

`src/content/` is persistent source content. Converter reruns are incremental by
default: they may overwrite generated Markdown, generated sidecars, and
converter-owned imported assets, but they must preserve unknown files in a work
folder. Full clean-room regeneration belongs in a scratch directory or an
explicit destructive maintenance command, not in the normal author workflow.

## Routing

- Real locale folders (`src/pages/ru/`, `src/pages/en/`), not a dynamic `[locale]` catch-all. The root `src/pages/index.astro` is the apex redirect stub Astro requires under prefix-all; `404.astro` is the generic host fallback alongside per-locale `ru/404.astro` and `en/404.astro`.
- Pages and downloads share a URL stem: `/ru/books/{slug}/` is the readable HTML; `/ru/books/{slug}.pdf`, `.epub`, `.md`, `.txt`, `.docx` are alternate representations.
- Download endpoints are Astro static endpoints (`[slug].[format].ts`). They emit existing bytes from the work bundle, or cheap text derivations from Markdown. They do not run document converters during the site build.

## Shared library

Route files are thin. The work lives in `src/lib/`:

- `works.ts` — discovery, per-language pairing, neighbor resolution from the public graph JSON.
- `downloads.ts` — format dispatch, artifact existence, cheap Markdown/TXT derivation.
- `i18n/` — locale config, URL routing, navigation labels, and UI copy dictionaries.
- `seo.ts` — canonical URLs, Open Graph metadata, JSON-LD builders.
- `conceptosphere.ts` — data adapters for the graph explorer.

If route code duplicates lookup, locale pairing, graph resolution, SEO metadata, or download dispatch, move it into `src/lib/`.

## Downloads

Per-work downloads are alternate representations of the work URL. The production
bulk surface at `/ru/downloads/` ships a single `all-md.zip` corpus archive; large
bulk PDF/EPUB archives are off-host archival/release artifacts, not production
site payload. Details: [`downloads.md`](./downloads.md).

PDF/EPUB rendering and DOCX optimization are local library work.
`pancratius downloads render` may refresh presentation artifacts before commit;
CI only packages and publishes what is already present in `src/content/`.

## Font assets

Three owners, no shared binary: a font's identity (family + OFL license) is shared, its bytes are not.

- **Site** (site build): self-hosted via Astro's Fonts API — no third-party request, no committed binary.
- **Downloads** (`pancratius/`): static faces Typst embeds into PDF/EPUB, matched by family name with system fonts ignored for reproducible renders.
- **Metrics** (lineation research package): the font the source was laid out in, vendored and hash-pinned so the wrap simulator measures real glyph advances.

## Search

Pagefind, built into the static output. Use defaults; Pagefind supports Russian with built-in stemming. Wrap each work body in `<article data-pagefind-body>` so the page-body is indexed cleanly. UI surfaces: inline filter on `/ru/books/` plus a per-locale `/ru/search/` page. Confirm real-corpus recall in QA before adding morphology hacks.

## Conceptosphere

One page at `/conceptosphere/`, two graph modes (Концепты, Книги), and one
merged algorithmic **Похожие книги** list on book pages. Data contract:
[`conceptosphere.md`](./conceptosphere.md).

## Voice / no-decoration constraints

- The site exists to be read. Components earn their place by serving the reading, not by performing.
- No analytics tracker, no auth, no comments, no payment provider.
- Corpus content is CC0. The license text lives in `LICENSE` and is restated
  in voice on `/license/`.

## What is not architecture

- Exact CLI invocations, version pins, font-installation steps, runner images. Those live in tooling docs and CI configs. If they change, this doc should not need to.
