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
- **License**: CC0 1.0 Universal across content and code.

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
  dynamic file endpoints such as `/books/{slug}.md` are not rewritten in dev.
- **Deploy targets.** The same `dist/` ships to a plain FTP host and to
  Cloudflare Pages. The FTP host sets `Content-Type` from file extension;
  Astro `Response` headers do not carry over to deployed static files.

## Content

- **Source of truth lives in `src/content/`** as Markdown plus sibling source/release artifacts (DOCX, PDF, EPUB, covers, images). See [`content-model.md`](./content-model.md).
- **Build-pipeline data lives in `data/`** and is **not** web-public. Astro's static build does not ship `data/`. Public payloads (graph JSON, etc.) are emitted or copied into `public/data/` so they appear in `dist/`.
- **Embedding caches, conversion manifests, and `conceptosphere-embed.json`** are build-time inputs only. Never publish them.

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

The converter records source DOCX paths and generated paths per work in
`data/conversion-manifest.json`; source filenames are provenance, not work
frontmatter. Four image roles cover the corpus, classified at conversion time
and recorded in the same manifest:

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

- Real locale folders (`src/pages/`, `src/pages/en/`), not a dynamic `[locale]` catch-all.
- Pages and downloads share a URL stem: `/books/{slug}/` is the readable HTML; `/books/{slug}.pdf`, `.epub`, `.md`, `.txt`, `.docx` are alternate representations.
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
bulk surface at `/downloads/` ships a single `all-md.zip` corpus archive; large
bulk PDF/EPUB archives are off-host archival/release artifacts, not production
site payload. Details: [`downloads.md`](./downloads.md).

PDF/EPUB rendering and DOCX optimization are local library work.
`pancratius downloads render` may refresh presentation artifacts before commit;
CI only packages and publishes what is already present in `src/content/`.

## Search

Pagefind, built into the static output. Use defaults; Pagefind supports Russian with built-in stemming. Wrap each work body in `<article data-pagefind-body>` so the page-body is indexed cleanly. UI surfaces: inline filter on `/books/` plus a global `/search/` page. Confirm real-corpus recall in QA before adding morphology hacks.

## Conceptosphere

One page at `/conceptosphere/`, two graph modes (Концепты, Книги), and one
merged algorithmic **Похожие книги** list on book pages. Data contract:
[`conceptosphere.md`](./conceptosphere.md).

## Voice / no-decoration constraints

- The site exists to be read. Components earn their place by serving the reading, not by performing.
- No analytics tracker, no auth, no comments, no payment provider.
- CC0 throughout. The license text lives in `LICENSE` and is restated in voice on `/license/`.

## What is not architecture

- Exact CLI invocations, version pins, font-installation steps, runner images. Those live in tooling docs and CI configs. If they change, this doc should not need to.
