# Pancratius Tooling & Invocation Contract

How Pancratius is operated from the command line — the invocation surface that
humans, agents (Codex/Claude), and the project's Claude/Codex skills call. Like
the sibling architecture docs, this describes the target contract; the path from
today's scattered entry points to it is in the Migration section at the end.

It is a companion to [`architecture.md`](./architecture.md) (what the site is),
[`content-model.md`](./content-model.md) (the corpus shape),
[`downloads.md`](./downloads.md) (the import/render/build split), and
[`audit-harness.md`](./audit-harness.md) (the audit contract).

## The problem this fixes

The repo grew ~40+ invocation points with no coherent front door: npm scripts,
~25 Python scripts run via `uv`, and Node `.ts` tools — plus a dozen
audits. A request like "import this DOCX as a new book" or "add this DOCX as a
project sub-page" has no obvious, discoverable command. The pain is often
misread as "we need one CLI for everything." That is the wrong fix: the site
build surface already exists and is correct; the **library-management side has
no door at all.**

## The decision: two doors, split by *mutate* vs *verify*

The boundary between the two doors is **not** subject (content vs site) and
**not** language (Python vs TS). It is **what the command does to the world**:

- **Library door — `pancratius` (uv) — MUTATE / PRODUCE the corpus.** Commands
  that *change* content or build inputs: import a work, scaffold a project
  sub-page, render release artifacts, optimize a DOCX, generate data products.
- **Site door — `npm` (Node) — BUILD the deployable artifact + VERIFY it.**
  `dev`, `build`, `preview`, and the *verification* family that gates the
  artifact: `astro check` (types), Playwright (smoke), and **audit** (contracts).

Verification is not mutation. `check`, `test`, and `audit` are pure — they never
change content; they decide whether the artifact is trustworthy to publish. By
that cut they belong with the build (the site door), alongside the type-check and
the smoke tests — not with the corpus-mutating commands. This keeps each door a
coherent essence: **`npm` = make and verify the site; `pancratius` = change the
library.** Two prefixes is the one accepted cost; the seam is real and
CI-enforced, so collapsing it would mean one language wrapping the other's
native work.

> A small companion note for `architecture.md`: the import/render/build split it
> already describes is the same seam — the library door is "import + render +
> data," the site door is "build + verify." Audit is verification, so it lives
> with the build.

## Site door — `npm`

Unchanged. `dev`, `build`, `preview`, `check`, `search:index` (Pagefind),
`test:smoke` (Playwright), the `prebuild:*` steps (build-time-coupled data prep),
and **`audit`** (the contract harness). This is the deploy path and what CI runs.
Do not wrap Astro under another tool — the site build is Node/CI-native and
correct as is.

### Audit lives here (verification), TS engine + Python checks

The audit harness's canonical surface is **`npm run audit`** (with
`audit:agent` / `audit:deploy` / `audit:content` modes per
[`audit-harness.md`](./audit-harness.md)), not a `pancratius` verb. Reasons:
audit is verification (site door, by the mutate/verify cut); most contracts it
checks live in Node/Astro surfaces (routes, `src/lib`, `dist`, sitemap/feed,
Pagefind, CSS, the built-surface crawl), so the *engine* is naturally TS; and a
TS engine belongs under the Node door, not fronted by a Python one. Shape:

```txt
scripts/audit/harness.ts   # runner, severity, report (canonical)
scripts/audit/rules/*.ts   # TS/Astro/CSS/HTML/dist/url/SSOT rules
scripts/audit/python/*.py  # existing content/corpus checks, called as subprocesses
```

The existing Python content audits are **not** rewritten for purity — the
harness calls them and normalizes their output over time. (This makes
`tooling.md` and `audit-harness.md` agree; the earlier "`pancratius audit`"
phrasing was wrong — audit is verification, so it is a site-door command.)

### Stack conformance also lives here (verification)

The stack-conformance checks (PAN016) are verification, so they are site-door
commands run in CI alongside the type-check:

- **TypeScript everywhere** — `check` (`astro check`) covers the app; the Node
  scripts and Playwright specs the app config excludes are covered by
  `typecheck:scripts` (`tsc -p tsconfig.scripts.json`). Editor association for
  those folders comes from `scripts/tsconfig.json` / `tests/tsconfig.json`.
- **Typed Python** — `lint:py` (`ruff` `ANN`: annotations present) and
  `typecheck:py` (`ty`: annotations correct); `check:py` runs both. Pinned in
  `uv.lock`, run via `uv run`; CI runs them `--frozen`. Tool rationale lives in
  [`decisions.md`](./decisions.md) ("Python type enforcement: ruff + ty").

## Library door — `pancratius`

A real package (flip `pyproject.toml` from `package = false` to a console-script
`pancratius = "pancratius.cli:main"`), invoked as `uv run pancratius …`,
**noun-first** groups (domain first, so `--help` is navigable and the ontology is
visible), built with `argparse` subparsers (no new dependency — the scripts
already use `argparse`).

| Command | Wraps (today) | Notes |
|---|---|---|
| `pancratius work import <docx> --kind book\|poem` | `import_docx.py` | Per-DOCX corpus-work importer. `--into <key>` adds a translation. |
| `pancratius project page add <slug> <docx>` | conversion lib | **Scaffolds** a draft sub-page (see below) — does not synthesize or auto-wire the landing. |
| `pancratius downloads render [--book N]` | `render_downloads.py` | Local PDF/EPUB/DOCX release artifacts. Never CI. |
| `pancratius docx optimize` | `docx_optimize.py` | In-place source DOCX cleanup. |
| `pancratius data graph generate` | `conceptosphere.py` | Regenerate graph data into `data/` (heavy, local). The CI-safe copy `data/`→`public/data/` is the npm `prebuild:graph-payloads`, a *separate* step — not this verb. |
| `pancratius data embed generate` | `conceptosphere_embed.py` | Regenerate embeddings into `data/` (heavy — needs `--extra embed`). |
| `pancratius data slug-map refresh` | `build_slug_map.py` | Regenerate the sitemap slug-map (same generator as `prebuild:slug-map` — one owner). |
| `pancratius data bulk refresh` | `build_bulk_archives.ts` | Rebuild `all-md.zip` (same as `prebuild:bulk-archives` — one owner; shells to Node). |

The verb space **teaches the corpus ontology** (see `content-model.md`): you
`work import` a book/poem (a corpus work, `(kind, number)`); you `project page
add` a sub-page (a themed section). An agent cannot express "import a project as
a book" — the boundary is in the grammar. `docx_to_md.py` is excluded by
omission — it gets no verb. The *file* cannot be deleted yet, though:
`scripts/lib/docx_conversion.py` imports it as `legacy` and delegates the whole
conversion engine to it, so `work import` depends on it transitively. Deletion is
gated on extracting those primitives into `scripts/lib/` (see Migration step 6).

### Mechanical (tool) vs editorial (skill)

The CLI does **mechanical** transforms only: DOCX→Markdown conversion (including
the verse/stanza-pairing rules from `content-model.md`), image
extraction/capping, frontmatter scaffolding, file placement. It does **not** do
**editorial composition**: deciding whether a document becomes a book, a
sub-page, or an inline section; the prophetic-voice synthesis; how a sub-page
sits in a project landing; the register (`Prose`/`Verse`) choice. Composition is
a **skill/prompt** concern (the agent's judgment), not a tool flag. This is why
`project page add` scaffolds and stops.

### `project page add` scaffolds, never synthesizes

It converts the DOCX, writes a **draft** sub-page bundle
(`src/content/projects/<slug>/subpages/<sub>/ru.md` with seeded frontmatter +
co-located cover), and **prints the suggested landing `subpages:` entry** for a
human/agent to place — it does **not** auto-edit the landing frontmatter.
Auto-wiring the landing would slide the tool toward a page-builder; projects are
authored sections, so placement, weight, and any synthesis stay editorial.

## Dependency model (concrete)

The console-script must not force every invocation to install heavy deps:

- **Minimal base deps** in `[project.dependencies]` (e.g. `pyyaml`, `pillow`) so
  the common verbs (`work import`, `downloads render`, `data slug-map`) install
  light.
- **Optional extras** for heavy stacks: `[project.optional-dependencies]
  embed = [mlx, …]` (system tools like pandoc/typst are not pip deps).
- **Lazy per-subcommand imports**: a subcommand imports its heavy modules inside
  its handler, and on `ImportError` exits with an actionable message
  (`this needs the embed extra: run "uv sync --extra embed"`).
- `uv` does **not** auto-install an extra per invocation. The contract is: sync
  the extra once, then run. The embedding pipeline (`data embed refresh`) is the
  one heavy verb — it requires `--extra embed`; if installing MLX as a pyproject
  extra ever proves hostile, the fallback is to keep that single pipeline a
  standalone PEP-723 script outside the console-script. The doc/`--help` must
  state whichever is chosen, not gesture at it.

## One owner, thin aliases

A task has exactly one implementation; a second surface is a thin alias, never a
copy. For `slug-map` and `bulk`, `prebuild:*` and `pancratius data … refresh`
call the **same** generator (one owner). Graph is two *distinct* activities, not
one: `prebuild:graph-payloads` only **copies** `data/`→`public/data/` (CI-safe),
while `pancratius data graph generate` **regenerates** the graph (heavy, local) —
do not collapse them. Cross-language
SSOTs (`kinds.ts`/`kinds.py`, `locales.ts`/`locales.py`) keep their parity
audits; the CLI adds no third copy.

## The two flagship flows (the import answer)

- **"Make this directory's DOCX + cover a new book":**
  `pancratius work import <docx> --kind book --lang ru --cover <cover>`, then
  the agent fills the seeded `description`/`tags` and confirms the title.
  (Importer polish surfaced during the #75 promotion: skip a leading
  cover-sized body image, don't repeat the title in-body, warn on a left-as-TODO
  description — so the seeded bundle is clean by default.)
- **"Add this DOCX as a new page in the Holy Rus project":**
  `pancratius project page add holy-rus <docx> --lang ru --title "…"` — converts
  (mechanical) and scaffolds a draft sub-page + prints the landing entry to add;
  the agent does the placement, register, and any synthesis (editorial).

## Skills connection

This surface **is** the API the Claude/Codex skills call, so it is optimized for
that. The skill is a thin doc naming verbs:

> Add a book: `uv run pancratius work import <docx> --kind book --lang <lang>`.
> Add a project page: `uv run pancratius project page add <slug> <docx> --lang <lang>`, then place the printed `subpages:` entry.
> Refresh downloads: `uv run pancratius downloads render [--book N]`.
> Check your work: `npm run audit`.
> Build the site: `npm run build`.
> Unsure of flags: `uv run pancratius <group> --help`.

Why it is a good skill API: stable verb-noun contract (survives refactors of the
underlying scripts); self-documenting via `--help` so the skill stays short and
an agent can recover from a stale doc by asking the CLI; one prefix per activity
(no `npm run x -- --flag` `--`-swallowing footgun); boundary-teaching (the work
vs section split is in the grammar); mechanical/editorial split is explicit (the
skill knows the CLI converts and the agent composes); and bootstrap-free (uv +
npm already required — no new toolchain). The CLI must therefore exist and
stabilize **before** the skills are written.

## Command inventory (the anchor for the verb map)

The verb map is derived from a complete inventory so the CLI does not become
another partial map. Disposition column: `npm` (site door), `work/project/…`
(library verb), `audit` (folds into the harness), `dev` (standalone diagnostic),
`retire` (no surface).

| Current entry point | Does | Mutates source? | Disposition |
|---|---|---|---|
| `npm run dev / build / preview / check` | build + dev + typecheck | no | `npm` (site) — unchanged |
| `npm run search:index` (pagefind) | build search index | no | `npm` (part of build) |
| `npm run test:smoke` (playwright) | smoke tests | no | `npm` (verify) |
| `npm run prebuild:slug-map` → `build_slug_map.py` | gen sitemap slug-map | no (gen `data/`) | `npm` prebuild + `pancratius data slug-map refresh` (one owner) |
| `npm run prebuild:graph-payloads` → `build_copy_graph_payloads.py` | **copy** graph JSON `data/`→`public/` | no | `npm` prebuild (CI-safe copy) — distinct from `data graph generate` |
| `npm run prebuild:bulk-archives` → `build_bulk_archives.ts` | build `all-md.zip` | no | `npm` prebuild + `pancratius data bulk refresh` |
| `import_docx.py` | DOCX → work bundle | **yes** (creates work) | `pancratius work import` |
| `docx_to_md.py` | legacy batch converter + **holds the conversion engine** (`lib/docx_conversion.py` imports it as `legacy`) | **yes** | no verb now; **delete only after extracting the engine into `scripts/lib/`** |
| `render_downloads.py` | render PDF/EPUB/DOCX | **yes** (release artifacts) | `pancratius downloads render` |
| `docx_optimize.py` | clean source DOCX in place | **yes** | `pancratius docx optimize` |
| `conceptosphere.py` | **generate** graph data | **yes** (gen `data/`) | `pancratius data graph generate` (local/heavy) |
| `conceptosphere_embed.py` | **generate** embeddings (MLX) | **yes** (gen `data/`) | `pancratius data embed generate` (`--extra embed`) |
| `sync_pagefind_dev.py` | copy pagefind index for dev | no | `npm run dev` helper (stays under dev) |
| `scripts/audit/*.py` | content/corpus + SSOT checks | no | `audit` — SSoT/asset/download/CI checks folded as fatal rules under `scripts/audit/python/`; content-quality ones folded as `audit:agent` heuristics (in place); `source_coverage.py` stays local-only (legacy-dependent, never CI). See `audit-harness.md` → "Relationship To Existing Audits" |
| `scripts/audit/run_all.ts` | audit aggregator | no | **retired** — `harness.ts` (`npm run audit`) is the aggregator |
| `download_asset_urls.py` | download/public-Markdown URL check | no | folded as PAN008 under `npm run audit:deploy` (post-build) |
| `tests/visual_audit.spec.ts` | visual regression GATE (console errors + mobile h-overflow, theme × viewport matrix) | no | `dev` (`npm run test:visual`; Playwright, `VISUAL_AUDIT=1`-gated, off the default smoke run) |
| `scripts/visual/{audit,viewport,shots}.ts` | snapshot GENERATORS → `.cache/visual-audit/` (gitignored) | no | `dev` (`npm run shots:audit` / `shots:viewport` / `shots:projects`) |
| `scripts/visual/lighthouse.ts` | Lighthouse perf REPORT (scorecard + `summary.json`) — **optional, networked** (fetches `lighthouse@13` on demand), not a CI/verification gate | no | `dev` (`npm run test:perf`) |
| `scripts/visual/harness.ts` + `harness.test.ts` | shared matrix/glue + pure-helper units | no | `dev` (units via `npm run test:unit`) |
| (none today) | add a project sub-page | **yes** (scaffold) | `pancratius project page add` (new) |

## Migration (incremental, not a prod blocker)

The site builds/deploys today via `npm`; library scripts run via `uv`. This is an
ergonomics + skills-enablement effort, sequenced before the skills.

1. **Document the contract** (this doc + inventory).
2. **Highest-value verbs first** over the existing `main(argv)` functions: flip
   `pyproject` to a console-script; a small `pancratius/cli.py` dispatcher;
   wire `work import` and `downloads render` (3–5 line facades, no rewrites),
   with the lazy-import / optional-extras dep model from the start.
3. **Stand up the audit harness** under `npm run audit` (TS core absorbing
   `run_all.ts`, Python checks subprocessed, Node crawler for `audit:deploy`).
   Update `audit-harness.md`'s CLI section to make `npm run audit` canonical.
4. **Add `project page add`** (scaffold-only) — the genuinely missing command.
5. **Wire data verbs:** `data slug-map/bulk refresh` as thin aliases over the
   prebuild generators (one owner); `data graph/embed generate` as the local
   heavy regen (distinct from the build-prep `prebuild:graph-payloads` copy).
6. **Extract the conversion engine** from `docx_to_md.py` into
   `scripts/lib/docx_conversion.py` (it currently does `import docx_to_md as
   legacy` and delegates `convert_docx_to_md`, `convert_poem_docx_to_md`, the AST
   verse/lineation normalizers, slug/image helpers, cross-ref restructuring) so
   `work import` stops depending on the legacy file — **then** delete
   `docx_to_md.py`. The verb surface is unchanged either way.

Do first: 2 + 3. Leave alone: the npm site door, `prebuild:*` coupling, the
`scripts/visual/` dev diagnostics, the shared `scripts/lib/` core.

## Rejected alternatives (so they are not relitigated)

- **One door for everything / Astro under a Python CLI** — inverts the
  Node/CI-native build into a leaky Python wrapper.
- **A Node/TS CLI owning the Python tools** — double-maintenance: re-declares
  every Python script's args and shells to `uv` for the real work. (The one
  place TS *is* the right owner — the audit engine — is the site door, not a
  library CLI.)
- **npm-only as the library API** — no per-command `--help`, weak args
  (`-- --flag` swallowing), a god-file of one-liners; a poor skill API.
- **`just` / Taskfile / Make** — a new toolchain for a `--list`; `pancratius
  --help` gives listing + `--help` + real args natively, no install.
- **A compiled Rust/Go binary** — a toolchain + release pipeline to ship a
  dispatcher; the runtime cost is pandoc/typst/ML, not arg parsing.
- **Status quo + a README index** — silent rot; nothing enforces the doc
  against the commands, which is what is already failing.
