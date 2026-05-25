# Pancratius Tooling & Invocation Contract

How Pancratius is operated from the command line — the invocation surface that
humans, agents (Codex/Claude), and the project's Claude/Codex skills call. Like the
sibling architecture docs this describes the target contract; what remains to build
it is at the end.

It is a companion to [`architecture.md`](./architecture.md) (what the site is, and
the import/render/build split), [`content-model.md`](./content-model.md) (the corpus
shape), [`import-pipeline.md`](./import-pipeline.md) (the DOCX→Markdown importer +
`WritePlan`/writer), [`downloads.md`](./downloads.md) (the release-artifact
contract), and [`audit-harness.md`](./audit-harness.md) (the audit contract).

## The problem this fixes

The repo grew ~40+ invocation points with no coherent front door: npm scripts,
~25 Python scripts run via `uv`, and Node `.ts` tools — plus the audits. A request
like "import this DOCX as a new book" or "add this DOCX as a project sub-page" has
no obvious, discoverable command. The pain is often misread as "we need one CLI for
everything." That is the wrong fix: the site build surface already exists and is
correct; the **library-management side has no door at all.**

## The decision: two doors, split by *mutate* vs *verify*

The boundary is **not** subject (content vs site) and **not** language (Python vs
TS). It is **what the command does to the world**:

- **Library door — `pancratius` (uv) — MUTATE / PRODUCE the corpus.** Commands that
  *change* content or build inputs: import a work, scaffold a project sub-page,
  render release artifacts, optimize a DOCX, generate data products.
- **Site door — `npm` (Node) — BUILD the deployable artifact + VERIFY it.** `dev`,
  `build`, `preview`, and the *verification* family that gates the artifact:
  `astro check` (types), Playwright (smoke), and **audit** (contracts).

Verification is not mutation. `check`, `test`, and `audit` are pure — they never
change content; they decide whether the artifact is trustworthy to publish. By that
cut they belong with the build (the site door), alongside the type-check and smoke
tests — not with the corpus-mutating commands. Each door keeps a coherent essence:
**`npm` = make and verify the site; `pancratius` = change the library.** Two
prefixes is the one accepted cost; the seam is real and CI-enforced.

## Site door — `npm`

Unchanged. `dev`, `build`, `preview`, `check`, `search:index` (Pagefind),
`test:smoke` (Playwright), the `prebuild:*` steps (build-time-coupled data prep),
and **`audit`** (the contract harness). This is the deploy path and what CI runs.
Do not wrap Astro under another tool — the site build is Node/CI-native.

### Audit lives here (verification)

The audit harness's canonical surface is **`npm run audit`** (with `audit:agent` /
`audit:deploy` modes and `audit:selftest`, per
[`audit-harness.md`](./audit-harness.md)), not a `pancratius` verb: audit is
verification (site door, by the mutate/verify cut), and most contracts it checks
live in Node/Astro surfaces (routes, `src/lib`, `dist`, sitemap/feed, Pagefind,
CSS, the built-surface crawl), so a TS engine belongs under the Node door. Shape:

```txt
scripts/audit/harness.ts   # runner, severity, report (canonical)
scripts/audit/rules/*.ts   # TS/Astro/CSS/HTML/dist/url/SSOT rules
scripts/audit/python/*.py  # content/corpus checks, called as subprocesses
```

The Python content audits are not rewritten for purity — the harness calls them as
subprocesses and normalizes their output.

### Stack conformance also lives here

The stack-conformance checks (PAN016) are verification, run in CI alongside the
type-check:

- **TypeScript everywhere** — `check` (`astro check`) covers the app; the Node
  scripts and Playwright specs the app config excludes are covered by
  `typecheck:scripts` (`tsc -p tsconfig.scripts.json`). Editor association comes
  from `scripts/tsconfig.json` / `tests/tsconfig.json`.
- **Typed Python** — `lint:py` (`ruff` `ANN`) and `typecheck:py` (`ty`); `check:py`
  runs both. Pinned in `uv.lock`, run `--frozen` in CI. Tool rationale in
  [`decisions.md`](./decisions.md) ("Python type enforcement: ruff + ty").

## Library door — `pancratius`

A real package with a console-script `pancratius = "pancratius.cli:main"`, invoked
as `uv run pancratius …`. Standing it up means adding `[project.scripts]` and a
`[build-system]` to `pyproject.toml` and removing `[tool.uv] package = false` (a
console-script needs a build backend; uv will not install an entry point while the
project is non-package). A small `pancratius/cli.py` argparse dispatcher with
**noun-first** groups (domain first, so `--help` is a navigable ontology); no new
dependency.

The dispatcher calls **library functions, not other CLIs.** Each owning script is a
library module exposing one clean typed entry — a function, or a frozen request
object where the argument set is large (`import_work(ImportRequest) -> WriteReport`
is the model). Its own `argparse main()` is a thin adapter or is dropped, so
`pancratius` is the only CLI surface. The dispatcher therefore owns one uniform
output contract: exit `0` success / `1` refusal-or-failure / `2` usage; human
summary on stdout, diagnostics on stderr. Library entries return values and raise;
they never print or `sys.exit`.

| Command | Library entry | Notes |
|---|---|---|
| `pancratius work import <docx> --kind book\|poem` | `import_work(ImportRequest)` | Per-DOCX corpus-work importer. `--into <key>` adds a translation. Flags map 1:1 onto `ImportRequest`. |
| `pancratius project page add <project> <subpage-slug> <docx>` | `scaffold_subpage(...)` | **Scaffolds** a draft sub-page (deterministic slice only) — never synthesizes or wires the landing. |
| `pancratius downloads render [--book N]` | `render_downloads` entry | Local PDF/EPUB/DOCX release artifacts. Never CI. |
| `pancratius docx optimize [paths…]` | `docx_optimize` entry | In-place source DOCX cleanup. |
| `pancratius data graph generate [--only concepts\|books]` | `conceptosphere` entry | Regenerate **both** graph projections into `data/` (heavy — `--extra graph`); `--only` for granular regen. The CI-safe `data/`→`public/data/` copy is the npm `prebuild:graph-payloads`, separate. |
| `pancratius data embed generate` | `conceptosphere_embed` entry | Regenerate embeddings into `data/` (heavy — `--extra embed`). |
| `pancratius data slug-map refresh` | `build_slug_map` entry | Sitemap slug-map (same generator as `prebuild:slug-map` — one owner). |
| `pancratius data bulk refresh` | `build_bulk_archives.ts` | `all-md.zip` (same as `prebuild:bulk-archives` — one owner; shells to Node, the one cross-language verb). |

The verb space **teaches the corpus ontology** (see `content-model.md`): you
`work import` a book/poem (a corpus work, `(kind, number)`); you `project page add`
a sub-page (a themed section). An agent cannot express "import a project as a book"
— the boundary is in the grammar (and PAN017-enforced). The DOCX→Markdown converter
is a library the `work import` path drives through the `scripts/lib/docx_conversion.py`
facade (`convert_single_docx`, the typed-IR pipeline `docx_adapter` → `ir_normalize`
→ `ir_lower`); it has no verb because it is not a user task.

### Mechanical (tool) vs editorial (skill)

The CLI does **mechanical** transforms only: DOCX→Markdown conversion (including the
verse/stanza rules from `content-model.md`), image extraction/capping, frontmatter
scaffolding, file placement. It does **not** do **editorial composition**: deciding
whether a document becomes a book, a sub-page, or an inline section; the
prophetic-voice synthesis; how a sub-page sits in a project landing; the register
(`Prose`/`Verse`) choice. Composition is a skill/agent's judgment, not a tool flag.
This is why `project page add` scaffolds and stops.

### `project page add` scaffolds only the deterministic slice

A project sub-page's value is editorial — projects were authored from
`docs/projects-plan.md` in the author's voice, not converted from a DOCX. So this
verb does the deterministic slice and stops: it converts the DOCX to a draft body,
co-locates images, and writes
`src/content/projects/<project>/subpages/<subpage-slug>/<lang>.md` with the
mechanical frontmatter (`kind`, `parent`, `slug`, `lang`) and the **editorial fields
(`title`, `description`, `weight`) as explicit `TODO` placeholders**. The draft is
intentionally schema-incomplete — it will not pass `npm run check` until a human
fills those fields. That is the safe choice: a draft that fails loudly beats one
that validates with a guessed `weight` and ships the wrong register. It then
**prints the suggested landing `subpages:` entry** for a human to place; it never
edits the landing.

It writes through the import **writer** (atomic, scoped, no-clobber, `--dry-run`).
The writer is a general safe-bundle-writer; the import *provenance* manifest is the
importer's concern, not the writer's, so a scaffold reuses the writer with no
import coupling (see [`import-pipeline.md`](./import-pipeline.md)).

## Dependency model

The console-script installs light; heavy stacks are opt-in:

- **Base deps** (`[project.dependencies]`, e.g. `pyyaml`, `pillow`) cover the
  in-process verbs: `work import`, `docx optimize`, `data slug-map refresh`,
  `project page add`.
- **Heavy stacks are `[project.optional-dependencies]` extras** — `graph`
  (networkx/igraph/leidenalg/…) and `embed` (MLX). The deps are declared here, in
  the project, **not** inlined per-script: these scripts are `pancratius` library
  modules, not standalone tools. (System tools like pandoc/typst are not pip deps.)
- The `data graph` / `data embed` handlers **lazy-import** their heavy library
  function inside the handler; on `ImportError` they exit with the hint
  (`run: uv sync --extra graph`). The light core never imports a heavy module, so
  `uv sync` without extras installs and runs the common verbs.

## One owner, thin aliases

A task has exactly one implementation; a second surface is a thin alias, never a
copy. For `slug-map` and `bulk`, `prebuild:*` and `pancratius data … refresh` call
the **same** generator. Graph is two *distinct* activities, not one:
`prebuild:graph-payloads` only **copies** `data/`→`public/data/` (CI-safe), while
`data graph generate` **regenerates** the graph (heavy, local) — do not collapse
them. Cross-language SSOTs (`kinds.ts`/`kinds.py`, `locales.ts`/`locales.py`) keep
their parity audits; the CLI adds no third copy.

## The two flagship flows (the import answer)

- **"Make this directory's DOCX + cover a new book":**
  `pancratius work import <docx> --kind book --lang ru --cover <cover>`, then the
  agent fills the seeded `description`/`tags` and confirms the title.
- **"Add this DOCX as a new page in the Holy Rus project":**
  `pancratius project page add holy-rus <subpage-slug> <docx> --lang ru` — converts
  (mechanical) and scaffolds a draft sub-page with editorial fields left `TODO`,
  then prints the landing entry to add; the agent does the title/description,
  placement, register, and any synthesis (editorial).

## Skills connection

This surface **is** the API the Claude/Codex skills call, so it is optimized for
that. The skill is a thin doc naming verbs:

> Add a book: `uv run pancratius work import <docx> --kind book --lang <lang>`.
> Add a project page: `uv run pancratius project page add <project> <subpage-slug> <docx> --lang <lang>`, then place the printed `subpages:` entry and fill the `TODO` fields.
> Refresh downloads: `uv run pancratius downloads render [--book N]`.
> Check your work: `npm run audit`.
> Build the site: `npm run build`.
> Unsure of flags: `uv run pancratius <group> --help`.

Why it is a good skill API: a stable verb-noun contract (survives refactors of the
library underneath); self-documenting via `--help` so the skill stays short and an
agent can recover from a stale doc by asking the CLI; one prefix per activity (no
`npm run x -- --flag` `--`-swallowing); boundary-teaching (work vs section is in the
grammar); explicit mechanical/editorial split; and bootstrap-free (uv + npm already
required). The CLI must exist and stabilize **before** the skills are written.

## What's left to build

The site builds/deploys via `npm`; the importer/IR pipeline and the audit harness
have landed. What remains is the library *door* itself:

1. **Stand up the console-script** — add `[project.scripts]` + a `[build-system]`,
   remove `[tool.uv] package = false`, add `pancratius/cli.py`.
2. **Make each owning script a library module** — extract one clean typed entry and
   reduce its `argparse main()` to a thin adapter (or drop it). `import_docx`
   already does this (`import_work`); `render_downloads`, `conceptosphere`,
   `conceptosphere_embed`, `docx_optimize`, `build_slug_map` follow the same shape
   so the dispatcher calls functions, not CLIs.
3. **Move heavy deps into extras** — lift the `graph`/`embed` stacks into
   `[project.optional-dependencies]`; re-lock `uv.lock`.
4. **Wire the verbs** — `work import`, `downloads render`, `docx optimize`, the
   `data …` verbs (thin aliases over the one owner for slug-map/bulk;
   lazy-imported heavy regen for graph/embed), and `project page add`
   (scaffold-only), with `scaffold_subpage` co-located with the conversion lib.
5. **Extend PAN017 to the CLI surface** — the work-kind guard scans `import_docx`
   today; it must also cover `pancratius/cli.py` (or the CLI must not redeclare
   `--kind`, deferring to the importer's entry) so the `book|poem` boundary stays
   audit-enforced.

Leave alone: the npm site door, `prebuild:*` coupling, the `scripts/visual/` dev
diagnostics, the shared `scripts/lib/` core.

## Rejected alternatives (so they are not relitigated)

- **One door for everything / Astro under a Python CLI** — inverts the
  Node/CI-native build into a leaky Python wrapper.
- **A `site` proxy group inside `pancratius` (e.g. `pancratius site audit` →
  `npm run audit`)** — a second surface for a one-owner command that puts a
  *verify* verb under the *mutate* door, inverting the doc's cut at the grammar
  level. Discoverability is a `--help` + skills-doc concern, not a routing one.
- **A Node/TS CLI owning the Python tools** — double-maintenance: re-declares every
  Python entry's args and shells to `uv` for the real work. (The one place TS *is*
  the right owner — the audit engine — is the site door.)
- **npm-only as the library API** — no per-command `--help`, weak args
  (`-- --flag` swallowing); a poor skill API.
- **`just` / Taskfile / Make** — a new toolchain for a `--list`; `pancratius --help`
  gives listing + `--help` + real args natively.
- **A compiled Rust/Go binary** — a toolchain + release pipeline to ship a
  dispatcher; the runtime cost is pandoc/typst/ML, not arg parsing.
- **Status quo + a README index** — silent rot; nothing enforces the doc against
  the commands.
