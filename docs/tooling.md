# Pancratius Tooling

## Boundary

Pancratius has one repository task surface over two native owners:

- `mise --locked run ...` develops, builds, checks, audits, and tests the
  repository. Mise owns the executable environment and cross-language task graph.
- `uv run pancratius ...` changes the library. It imports, scaffolds, renders
  release artifacts, optimizes source DOCX, and regenerates committed Python data
  products.
- `npm run ...` implements the Astro/TypeScript site subtree. It owns Pagefind,
  Playwright, TypeScript checks, and deterministic build derivations; uv owns
  Python dependency resolution and implementation commands.

The owner is decided by the effect. Mutation and committed corpus products belong
to `pancratius`; owner-local commands stay with npm or uv; orchestration that
crosses owners belongs to mise.

`intent-ai/` is a downstream research package, not a third production CLI. Run it through its
locked uv project. It may write its own annotation evidence, derived record cache, and explicit
correction sidecars; it does not own DOCX parsing or site builds.

Do not add proxy commands between native owners. Mise may expose a small set of
repository workflows that delegate to native leaves; it must not mirror every npm
script. In particular, `pancratius` must not grow `audit`, `check`, `test`,
`build`, `dev`, `preview`, or `site` commands. PAN019 guards this.

`mise tasks` is the live command catalog. `verify` is portable and excludes
Pandoc-marked tests; `verify:toolchain` runs those tests plus the PDF/EPUB smoke.
Independent check lanes may run together, but build, post-build audit, and
Playwright stay ordered because they share `dist/`.

## Site Operations

Site operations are npm-native because the deploy artifact is an Astro static
site.

| Command | Owns |
| --- | --- |
| `npm run dev` | Generate deterministic site inputs, sync Pagefind for local dev, start Astro dev server. |
| `npm run build` | Generate deterministic site inputs, build the static site, then build the Pagefind search index. |
| `npm run generate` | Build-time derivations: slug map, public graph payloads, and bulk archive manifest/cache. |
| `npm run preview` | Astro preview of the built site. |
| `npm run check:site` | Generate site inputs, then run Astro content/type checks. |
| `npm run check:code` | Type-check TS tooling, lint code and styles, run Knip, and run Node unit tests. |
| `npm run check:unused` | Knip unused file/dependency/export analysis for the site/tooling surface. |
| `npm run lint` | ESLint plus Stylelint. |
| `npm run lint:code` | ESLint over site, build, audit, and test TypeScript/Astro/JavaScript. |
| `npm run lint:style` | Stylelint over CSS files and Astro component style blocks. |
| `npm run audit:deps` | npm vulnerability audit at the high-severity release gate. |
| `npm run audit:css-values` | Diagnostic PostCSS report for repeated CSS literals and layout/spacing/type drift. |
| `npm run audit:layout-fill` | Diagnostic Playwright sweep for under-filled reading columns; run against a local site with `BASE_URL` or the default `http://localhost:4321`. |
| `npm run test:e2e` | Playwright e2e specs. |
| `npm run test:visual` | Playwright visual gate. |

Build derivations live in `build/` and run from npm. They derive artifacts from
committed source; they do not mutate `src/content/`:

- `build/slug-map.ts` writes the build-time route manifest.
- `build/copy-graph-payloads.ts` minifies committed graph JSON into the public
  payload space.
- `build/bulk-archives.ts` builds the bulk archive manifest and `.cache` zip.
- `build/sync-pagefind-dev.ts` copies Pagefind output for local dev.

Audit is a repository verification subtree, not a site-only concern. Mise invokes
its Node entry point; the harness runs TypeScript rules and delegates parser-heavy
checks to uv-managed Python without exposing each rule as a separate task.

## Lineation Research

```sh
uv run --project intent-ai --frozen python -m intent_ai.build_records
uv run --project intent-ai --group dev --frozen pytest intent-ai/tests -q -c intent-ai/pyproject.toml
```

The first command rebuilds ignored records for every edition referenced by committed annotations;
it reads committed DOCX files and does not import or mutate library content. The second is the
local acceptance gate: portable tests plus corpus/cache and repository-history checks.
`mise --locked run check:intent-ai`, included in the repository check graph, runs
the portable subset without compiling DOCX or depending on ignored local state.

## Library Operations

Library operations use the `pancratius` Python package:

```sh
uv run pancratius <group> ...
```

It is a real console script (`pancratius.cli:main`). The dispatcher calls library
functions in process. It does not shell out to other Python CLIs.

| Command | Owner |
| --- | --- |
| `pancratius work import <docx> (--kind book|poem | --to book:NN|poem:NN)` | `pancratius.import_docx.import_work` |
| `pancratius work translate [book:NN …] [--dry-run]` | `pancratius.translation.text.translate_book` |
| `pancratius image translate <book:NN|project:slug[/subpage] …> [--dry-run] [--json] [--replace]` | `pancratius.translation.image` providers + engine |
| `pancratius project page add <project:slug/subpage> <docx>` | `pancratius.docx_conversion.scaffold_subpage` |
| `pancratius video sync [--channel KEY] [--dry-run]` | `pancratius.video_scan.scan` |
| `pancratius downloads render [book:NN|poem:NN …] [--dry-run] [--json]` | `pancratius.render_downloads` |
| `pancratius docx optimize [paths...]` | `pancratius.docx_optimize` |
| `pancratius docx inspect <book:NN|docx> [--contains TEXT|--around TEXT|--range LO:HI|--verse-only|--lineated-only]` | `pancratius.docx_inspect` |
| `pancratius docx render-slice <book:NN|docx> (--around TEXT|--range LO:HI) --out <png>` | `pancratius.docx_render` |
| `pancratius docx merge <parts...> --out <docx> [--part TITLE::MARKER]` | `pancratius.docx_merge` |
| `pancratius docx roundtrip-md [book:NN] [--lang LOCALE] [--json]` | `pancratius.docx_roundtrip` |
| `pancratius docx translate-from-md [book:NN] [--lang en] [--backend transfer|markdown-render] [--dry-run] [--replace]` | `pancratius.translation.docx` |
| `pancratius conceptosphere graph generate [--only concepts|books]` | `pancratius.conceptosphere.generate_graph` |
| `pancratius conceptosphere embed generate` | `pancratius.conceptosphere_embed.generate_embeddings` |

The grammar carries the content model:

- Existing library resources are named with typed selectors such as `book:50`,
  `poem:1`, `project:holy-rus`, and `project:holy-rus/tartaria`. Primary
  resource identities are positional; flags remain options like `--dry-run`,
  `--replace`, `--lang`, and `--output-dir`. Source-first creation commands are
  the exception: `work import <docx> --kind book|poem` keeps the external artifact
  as the primary positional input; `work import <docx> --to book:50` names the
  destination identity when the number is explicit. `--kind` and `--to` are
  mutually exclusive, `--to` infers both kind and number, and `--replace` is
  required only when the concrete locale file would be overwritten.
- `work import` handles corpus works only: books and poems. `project` and
  `video` are routed but not works; PAN017 guards this.
- `work translate` drafts an `en.md` from a book's `ru.md` via OpenRouter
  (`OPENROUTER_API_KEY`), preserving source structure and lineation and recording
  `translation.source: ai`. `--dry-run` prints the plan and a live-priced cost
  estimate without an API key. Each output chunk receives the book brief and a
  bounded, reusable part of the exact source as context. Successful chunks and the profile brief are cached
  under `.cache/translate/` (gitignored); a re-run replays successful chunks for
  free and re-attempts only the chunks that never produced a complete translation.
  Pass `--no-cache` to bypass read and write (always calls the API). To clear the
  cache: `rm -rf .cache/translate/`.
- `image translate` translates visible text in text-bearing image assets while
  preserving the image itself. The engine is content-agnostic: providers resolve
  source/target paths, expected visible text, and source-keyed overrides, then
  the engine runs vision recon → image edit → vision QA with a retry steering
  loop. Selectors include book covers (`book:50`) and project covers
  (`project:holy-rus`, `project:holy-rus/tartaria`). Expected text may be matched
  by exact source text, normalized source text, or detected image role
  (`primary`, `tagline`, etc.) and may be synthesized when recon misses it.
  Source-text expected matches may claim a consecutive detected text block, so a
  phrase split across visual lines is replaced as one semantic element. Overrides
  are conditional: they only apply to source text recon actually sees. There is no
  engine-level book, title, or author special case. If an existing target image
  exists it is QA-d first; PASS → done without regeneration. Failed existing
  target QA refuses regeneration unless `--replace` is passed. `--dry-run`
  resolves provider inputs and prints reads/writes without an API key or writes.
  Live runs require `OPENROUTER_API_KEY`.
- `project page add` scaffolds a project sub-page draft. It does not edit the
  project landing and does not decide the page's editorial placement. The
  destination must be a project sub-page selector such as
  `project:holy-rus/tartaria`; project landing selectors are not valid here.
- `video sync` polls every `scan: true` channel in
  `src/content/videos/channels.yaml` via the YouTube Data API v3 (requires
  `YOUTUBE_API_KEY`) and scaffolds frontmatter + a `cover.<lang>.jpg` thumbnail
  for each new video. Known playlist IDs seed localized tags through
  `data/tag-glossary.yaml`; titles are never matched. Unknown playlists are
  ignored, so creating or renaming a YouTube playlist cannot block publication.
  Only tags are saved; existing entries and their manual edits are left alone.
  Mapping changes affect future imports. The title's trailing discovery hashtags
  are dropped. The raw YouTube description is discovery copy, not reading copy, so it is not dumped
  into the page: `pancratius.video_description` splits it (via OpenRouter,
  `OPENROUTER_API_KEY`) into a clean `description` (the hook) and a Markdown
  reading `body` — a faithful draft of the author's own words with the promo
  footer, links, and SEO cruft removed, gated by a deterministic QA check (no
  junk, grounded in the source, right language). This is the same class as `work
  translate` drafting an AI translation: a reviewable draft, not an editorial
  decision, and never a raw dump. When the author published an `en-US`
  localization, a paired `en.md` is scaffolded the same way (`translation.source:
  literary`, terminology and curly quotes normalized to the library's English
  conventions). With no `OPENROUTER_API_KEY`
  the split falls back to a deterministic clean-and-strip. Re-runs never touch
  known entries. PAN028 guards committed hooks against junk.
- `docx inspect` is read-only source diagnostics. It prints per-paragraph OOXML
  and importer classification signals for debugging DOCX import behavior. When
  repeated text maps to multiple IR roles, it reports the row as ambiguous
  rather than guessing; when IR blocks carry source provenance, merged block spans
  appear as `ir=LO..HI` in the signal column. It does not create, edit, optimize,
  or convert corpus files.
- `docx render-slice` is an explicit visual QA diagnostic, not a download renderer
  and not a render of the original page. It builds a temporary DOCX containing the
  selected paragraph range, asks LibreOffice to render that slice, and rasterizes
  the PDF to PNG for human comparison with `docx inspect`. `--around` must match
  exactly one paragraph; use `docx inspect --around` first and then `--range` for
  repeated text.
- `docx merge` composes the source parts and validates the resulting DOCX package
  structure (ZIP, XML, relationships, media references). Optional
  `--part TITLE::MARKER` arguments insert real source part headings during the
  merge. Office-suite load checks are outside the first-class merge path; use
  explicit local QA when that heavier confidence is needed.
- `docx roundtrip-md` is a DOCX-source round-trip check: it copies the content tree to a
  temp root, imports committed `<lang>.docx` files through the normal work
  importer with replacement enabled only inside that temp root, and compares the
  generated `<lang>.md` against the committed Markdown. It does not mutate
  `src/content`. The default text report is for humans; `--json` is for agents
  and corpus repair loops. Human-visible text drift, public frontmatter drift,
  importer refusal, and newly introduced mixed-script English text fail the
  command; harmless Markdown serialization and typography drift are reported but
  non-fatal. Run it locally for the locale you changed when changing DOCX,
  import, translation, or content transfer behavior.
- `docx translate-from-md` bootstraps a missing translated DOCX from a committed
  `ru.docx`, its imported `ru.md`, and an aligned target Markdown file such as
  `en.md`. The default `--backend transfer` uses Pandoc only as a Markdown AST
  reader, transfers target text into the existing Word paragraph/run structure,
  and refuses documents whose nonblank Markdown units cannot be aligned safely.
  `--backend markdown-render` is an explicit fallback for structurally
  incompatible books: it renders target Markdown with Pandoc, using `ru.docx`
  only as a reference style document, and warns that donor paragraph/run
  structure was not transplanted. After a translated DOCX exists, that DOCX is
  source; Markdown should be re-derived through the normal importer. `--replace`
  therefore requires an explicit `book:NN` selector. The command does not
  translate text or make editorial repairs to mismatched corpora. The stable
  transfer contract is in
  [`pancratius/translation/docx/README.md`](../pancratius/translation/docx/README.md).
- Graph and embedding generation live here because they produce committed
  Python-only data products. Copying those products into `public/data/` is npm
  build work.

DOCX diagnostic examples:

```sh
# Grep-like source/importer inspection around text in a committed book source.
uv run pancratius docx inspect book:30 --around "Если готов" --context 8

# Render the same neighborhood as an isolated visual slice for human comparison.
uv run pancratius docx render-slice book:30 --around "Если готов" \
  --context 8 --out /tmp/book30-ready.png

# Inspect a precise paragraph range, then render exactly that range.
uv run pancratius docx inspect book:25 --range 180:205
uv run pancratius docx render-slice book:25 --range 180:205 \
  --out /tmp/book25-180-205.png
```

DOCX merge example:

```sh
uv run pancratius docx merge \
  ~/Downloads/book-02-ru-part1.docx \
  ~/Downloads/book-02-ru-part2.docx \
  ~/Downloads/book-02-ru-part3.docx \
  --out /tmp/book-02-ru-merged.docx \
  --part "Часть 1::Глава 1. Который видит Свет" \
  --part "Часть 2::Глава 1. Где заканчивается" \
  --part "Часть 3::Глава 1. Школа"
```

## Exit And Output Contract

The `pancratius` CLI owns process behavior:

- `0`: success.
- `1`: failed operation or refused write.
- `2`: usage/input error.

Library owners return values or raise domain exceptions. They do not call
`sys.exit`, parse shell flags, or print progress meant for another command
surface. The CLI turns library outcomes into user-facing text and exit codes.

## Mechanical vs Editorial

`pancratius` performs mechanical work:

- convert DOCX to canonical Markdown through the import IR;
- transfer translated Markdown text into an aligned source DOCX structure;
- extract and cap images;
- preserve or seed frontmatter;
- place files in the correct bundle;
- render release artifacts;
- regenerate graph/embed data.

It does not make final editorial decisions: whether a document deserves to be a
book, the final title, composing project landings, ordering project sub-pages,
approving translations, or changing theological register. When a tool cannot
know, it produces a diagnostic or a draft, not a guess.

A tool MAY produce an AI-authored *draft* of a field a human then owns, as long
as it is faithful, provenance-marked, and reversible — not an irreversible
decision. `work translate` drafts an `en.md` marked `translation.source: ai`;
`video sync` drafts a video's `description` + reading `body` from the raw YouTube
description. Both are transformations of the author's own words gated by a
mechanical QA check, not the CLI inventing content. The line is decision vs
draft, not machine vs human.

## Dependencies

Base Python dependencies cover the light local verbs. Heavy graph and embedding
stacks are optional extras:

```sh
uv sync --extra graph
uv sync --extra embed
```

The CLI lazy-imports those owners and prints the relevant extra hint when the
stack is missing. Pandoc and Typst come from mise; there is no embedded fallback.
DOCX import does not require Pandoc, while LibreOffice remains an external
desktop dependency. Run `mise --locked run verify:toolchain` for renderer
changes. Node and Python major updates must also update their compatibility
declarations and locks.

## Invariants

- One task has one owner. A second command surface is drift unless it is only a
  deliberate repository-level composition in mise.
- CI never renders PDF/EPUB, optimizes DOCX, imports DOCX, or regenerates
  embeddings.
- The Python package does not reach into `build/` or `audit/` to implement site
  work.
- The site build does not shell into `pancratius` to manufacture corpus source.
- Cross-language facts, such as locales and kind segments, have explicit parity
  audits rather than hidden copies.
