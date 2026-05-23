# Pancratius Architecture

The top-level contract for the production site. Implementation details (commands, versions, runner images) live in scripts and CI configs, not here.

## Core distinctions

The boundary cuts the rest of the architecture rests on. Each is owned by the
linked doc; this is only the index.

- **Content types — population vs individual vs section.** Books and poems are a
  *population* (a collection: one shape, paired by `(kind, number)`, full
  downloads); pages are *individuals* (one-of-a-kind dedicated routes); projects
  are *themed sections* (mini-sites), not works. → [`content-model.md`](./content-model.md).
- **Mutate vs verify — the two command doors.** `pancratius` (uv) *mutates/produces*
  the corpus (import, render, data); `npm` *builds and verifies* the site
  (`build`, `check`, smoke, `audit`). Verification is not mutation, so audit lives
  with the build. → [`tooling.md`](./tooling.md).
- **Import / render / build — three activities.** Import DOCX → source; render
  release artifacts locally; build/publish the static site in CI. CI never
  imports or renders. → [`downloads.md`](./downloads.md).
- **Mechanical vs editorial.** The CLI does mechanical transforms (DOCX→Markdown,
  verse pairing, image capping, scaffolding); composition/synthesis is agent/skill
  judgment, never a tool flag. → [`tooling.md`](./tooling.md).
- **Display fallback vs route existence.** Derived display data may fall back to
  the default locale; a route/download/feed/sitemap entry exists only when that
  locale was authored — never render default-locale body under `/en/…`.
  → [`i18n-routing.md`](./i18n-routing.md).
- **`<Prose>` vs `<Verse>` — two body-renderer components, no "register" enum.**
  The component is the register; a `class` prop carries page-local looks. Used by
  static pages + project sub-pages today (work pages still render the prose
  register directly — a follow-up). → [`decisions.md`](./decisions.md).

## Stack

- **Framework**: Astro 6+. No additional UI framework — no React, no Vue, no Svelte, no Tailwind. Vanilla CSS scoped via Astro components.
- **Language**: **TypeScript 6+, strict mode, everywhere in production source.** No handwritten production JavaScript (`.js` / `.mjs` / `.cjs`); archived `legacy/` and `design/` prototypes stay excluded until they are deleted. Astro config is `astro.config.ts`.
- **Runtime**: Node 24.
- **Scripts language**: Python 3.13+ with type hints. **Run via `uv` only**; project Python dependencies are locked in `pyproject.toml` / `uv.lock`. No `pip install`, no `conda`, no `requirements.txt`.
- **Conceptosphere viz libs**: Sigma 3 + `graphology` + `graphology-layout-forceatlas2`, bundled via npm, not CDN.
- **Search**: Pagefind (static; ships with the build).
- **Library-management tools**: pandoc, typst, pymorphy3, Qwen3-Embedding via MLX. These are local/admin tools for importing content, refreshing downloads, and regenerating data products. They are not part of the site deploy path.
- **License**: CC0 1.0 Universal across content and code.

## Shape

- **Static site.** Astro `output: 'static'`. The build emits files; the host serves files. No SSR, no runtime backend, no API surface.
- **CI builds and publishes the site only.** CI validates content, builds Astro, runs Pagefind/sitemap generation, checks that referenced artifacts exist, and deploys `dist/`. It does not manufacture the library: no DOCX optimization, no PDF/EPUB rendering, no embedding regeneration.
- **One URL = one resource.** Language, content, downloads, alternate-language links all follow from the URL. No decoupled "UI language" state. See [`i18n-routing.md`](./i18n-routing.md).
- **Canonical URLs end in `/` for HTML, in the extension for files.** The canonical shape is produced by shared URL helpers. Astro config uses `trailingSlash: "ignore"` so dynamic file endpoints such as `/books/{slug}.md` are not rewritten to `/books/{slug}.md/` in dev.
- **Deploy target shape.** A plain file host reached over FTP. Trust the server's MIME table — it picks `Content-Type` by file extension. Don't try to set headers from Astro `Response`s; they don't propagate to the deployed file.
- **Mirror.** A GitHub Pages mirror uses the same source and build pipeline, with deploy-target-specific `site` / `base` config (the mirror's URL prefix differs, so it produces its own `dist/`).

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
future author should be able to add one book by editing one work folder.

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

The implementation can use Astro's image pipeline, a small custom copy/optimize
step, or both. The architecture-level invariant is not the mechanism; it is that
source assets are co-located with the work, and generated public assets are build
output.

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
- `i18n.ts` — locale config, slug pairing, hreflang generation.
- `seo.ts` — canonical URLs, Open Graph metadata, JSON-LD builders.
- `conceptosphere.ts` — data adapters for the graph explorer.

If route code duplicates lookup, locale pairing, graph resolution, SEO metadata, or download dispatch, move it into `src/lib/`.

## Downloads

Per-work downloads are alternate representations of the work URL. The production
bulk surface at `/downloads/` ships a single `all-md.zip` corpus archive; large
bulk PDF/EPUB archives are off-host archival/release artifacts, not production
site payload. Details: [`downloads.md`](./downloads.md).

PDF/EPUB/DOCX management is local library work. A non-developer-friendly script may refresh those artifacts before commit, but CI should only package and publish what is already present in `src/content/`.

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

- Exact CLI invocations, version pins, font-installation steps, runner images. Those live in scripts and script docs. If they change, this doc should not need to.
