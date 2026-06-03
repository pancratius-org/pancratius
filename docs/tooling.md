# Pancratius Tooling

## Boundary

Pancratius has two command surfaces:

- `uv run pancratius ...` changes the library. It imports, scaffolds, renders
  release artifacts, optimizes source DOCX, and regenerates committed Python data
  products.
- `npm run ...` builds, serves, and verifies the site. It owns Astro, Pagefind,
  Playwright, TypeScript checks, audit, and deterministic build derivations.

The owner is decided by the effect. Mutation and committed corpus products belong
to `pancratius`; build and verification belong to `npm`.

Do not add wrapper commands across that boundary. In particular, `pancratius`
must not grow `audit`, `check`, `test`, `build`, `dev`, `preview`, or `site`
commands. PAN019 guards this.

## Site Operations

Site operations are npm-native because the deploy artifact is an Astro static
site.

| Command | Owns |
| --- | --- |
| `npm run dev` | Prebuild derivations, Pagefind dev sync, Astro dev server. |
| `npm run build` | Prebuild derivations, static build, Pagefind index. |
| `npm run preview` | Astro preview of the built site. |
| `npm run check` | Prebuild derivations plus Astro content/type checks. |
| `npm run lint` | ESLint over site, build, audit, and test TypeScript/Astro/JavaScript. |
| `npm run lint:css` | Stylelint over CSS files and Astro component style blocks. |
| `npm run dead` | Knip dead-code/dependency/export analysis for the site/tooling surface. |
| `npm run check:js` | Type-check TS tooling, lint TS/CSS, run Knip, and run Node unit tests. |
| `npm run check:ts` | Focused TS tooling type-check plus Node unit tests. |
| `npm run verify` | Local equivalent of the PR gate (`pr.yml` minus Playwright). |
| `npm run audit` | Architectural contract harness; see [`audit-harness.md`](./audit-harness.md). |
| `npm run audit:agent` | Core audit plus non-blocking heuristic checks. |
| `npm run audit:post-build` | Rules that need an emitted `dist/` (PAN014 link crawl, PAN008 `/assets/` URL contract). |
| `npm run audit:selftest` | Harness fixtures proving audit polarity. |
| `npm run test:smoke` | Playwright smoke/e2e specs. |
| `npm run test:visual` | Playwright visual gate. |
| `npm run check:py` | Ruff annotations + `ty` types + pytest behaviour for the Python tooling. |

Build derivations live in `build/` and run from npm. They derive artifacts from
committed source; they do not mutate `src/content/`:

- `build/slug-map.ts` writes the build-time route manifest.
- `build/copy-graph-payloads.ts` minifies committed graph JSON into the public
  payload space.
- `build/bulk-archives.ts` builds the bulk archive manifest and `.cache` zip.
- `build/sync-pagefind-dev.ts` copies Pagefind output for local dev.

Audit belongs to site operations because it verifies. Python checks in `audit/`
are subprocesses of the harness, not standalone commands.

## Library Operations

Library operations use the `pancratius` Python package:

```sh
uv run pancratius <group> ...
```

It is a real console script (`pancratius.cli:main`). The dispatcher calls library
functions in process. It does not shell out to other Python CLIs.

| Command | Owner |
| --- | --- |
| `pancratius work import <docx> --kind book|poem` | `pancratius.import_docx.import_work` |
| `pancratius project page add <project> <subpage-slug> <docx>` | `pancratius.docx_conversion.scaffold_subpage` |
| `pancratius video sync [--channel KEY] [--dry-run]` | `pancratius.video_scan.scan` |
| `pancratius downloads render [--book N]` | `pancratius.render_downloads` |
| `pancratius docx optimize [paths...]` | `pancratius.docx_optimize` |
| `pancratius docx inspect <docx> [--contains TEXT|--around TEXT|--range LO:HI|--verse-only|--lineated-only]` | `pancratius.docx_inspect` |
| `pancratius docx render-slice <docx> (--around TEXT|--range LO:HI) --out <png>` | `pancratius.docx_render` |
| `pancratius docx merge <parts...> --out <docx> [--part TITLE::MARKER]` | `pancratius.docx_merge` |
| `pancratius conceptosphere graph generate [--only concepts|books]` | `pancratius.conceptosphere.generate_graph` |
| `pancratius conceptosphere embed generate` | `pancratius.conceptosphere_embed.generate_embeddings` |

The grammar carries the content model:

- `work import` handles corpus works only: books and poems. `project` and
  `video` are routed but not works; PAN017 guards this.
- `project page add` scaffolds a project sub-page draft. It does not edit the
  project landing and does not decide the page's editorial placement.
- `video sync` is mechanical-only: it polls every `scan: true` channel in
  `src/content/videos/channels.yaml` via the YouTube Data API v3 (requires
  `YOUTUBE_API_KEY`) and scaffolds frontmatter + a `cover.<lang>.jpg`
  thumbnail for each new video. Commentary in the body is editorial. Re-runs
  never touch known entries.
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
- Graph and embedding generation live here because they produce committed
  Python-only data products. Copying those products into `public/data/` is npm
  build work.

DOCX diagnostic examples:

```sh
# Grep-like source/importer inspection around text in a committed book source.
uv run pancratius docx inspect --book 30 --around "Если готов" --context 8

# Render the same neighborhood as an isolated visual slice for human comparison.
uv run pancratius docx render-slice --book 30 --around "Если готов" \
  --context 8 --out /tmp/book30-ready.png

# Inspect a precise paragraph range, then render exactly that range.
uv run pancratius docx inspect --book 25 --range 180:205
uv run pancratius docx render-slice --book 25 --range 180:205 \
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
- extract and cap images;
- preserve or seed frontmatter;
- place files in the correct bundle;
- render release artifacts;
- regenerate graph/embed data.

It does not perform editorial judgment: deciding whether a document deserves to
be a book, choosing final titles/descriptions, composing project landings,
ordering project sub-pages, approving translations, or changing theological
register. When a tool cannot know, it should produce a diagnostic or a draft
placeholder, not guess.

## Dependencies

Base Python dependencies cover the light local verbs. Heavy graph and embedding
stacks are optional extras:

```sh
uv sync --extra graph
uv sync --extra embed
```

The CLI lazy-imports those owners and prints the relevant extra hint when the
stack is missing. System tools such as pandoc and typst are local prerequisites,
not Python package dependencies.

## Invariants

- One task has one owner. A second command surface is drift unless it is only a
  documented alias at the same command surface.
- CI never imports DOCX, renders PDF/EPUB, optimizes DOCX, or regenerates
  embeddings.
- The Python package does not reach into `build/` or `audit/` to implement site
  work.
- The site build does not shell into `pancratius` to manufacture corpus source.
- Cross-language facts, such as locales and kind segments, have explicit parity
  audits rather than hidden copies.
