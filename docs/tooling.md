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
| `npm run build` | Prebuild derivations, `astro check`, static build, Pagefind index. |
| `npm run preview` | Astro preview of the built site. |
| `npm run check` | Prebuild derivations plus Astro content/type checks. |
| `npm run audit` | Core architecture audit. |
| `npm run audit:agent` | Core audit plus non-blocking heuristic checks. |
| `npm run audit:deploy` | Deploy-surface audit against emitted `dist/`. |
| `npm run audit:selftest` | Harness fixtures proving audit polarity. |
| `npm run test:smoke` | Playwright smoke/e2e specs. |
| `npm run test:unit` | Node unit tests for TS helpers. |
| `npm run test:visual` | Playwright visual gate. |
| `npm run typecheck:tooling` | Type-check TS tooling outside the Astro app config. |
| `npm run check:py` | Ruff annotation lint plus `ty` for Python. |

Build derivations live in `build/` and run from npm. They derive artifacts from
committed source; they do not mutate `src/content/`:

- `build/slug-map.ts` writes the build-time route manifest.
- `build/copy-graph-payloads.ts` copies committed graph data into the public
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
- Graph and embedding generation live here because they produce committed
  Python-only data products. Copying those products into `public/data/` is npm
  build work.

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
