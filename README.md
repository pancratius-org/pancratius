# Pancratius

Static CMS and open digital library for the work of my good friend Sergey
Orekhov (pen name Pancratius). The site is Astro/TypeScript. The library tooling is
Python/uv. The output is plain static files with Pagefind search, downloads, concept
graph analytics,  multilingual authored or synced content, and recommendations.

The repo is built around the constraint that it will be maintained by
a non-technical author working through AI coding agents. As review cannot
rely on a human reading the diff, the usual safety net is gone. One of the
goals of the tooling is to prevent drift caused by semi-supervised long loops.

## The Shape

- Author writes in DOCX.
- DOCX is the source. Markdown, public Markdown, TXT, PDF, EPUB, archives, graph
  payloads, and recommendation data are derived.
- Author manages his works using local AI assistant app, not a browser CMS.
- Multi-repository workflows are inconvenient for the author, thus monorepo.
- The public site is static: no database, no admin backend, trivial to host.
- CI verifies and publishes committed source, syncs videos. It does not import 
  works, render books, optimize DOCX, or regenerate embeddings.

Two command surfaces has an explicit boundary:

```bash
uv run pancratius ...  # changes the library and derivations like graph payloads
npm run ...            # checks, builds, audits, previews, deploys the site
```

## Structure and Intent Restoration

The idea was simple: take a 2GB archive of DOCX books and poetry, optimize in-place,
extract images, host books so people could read them.

The corpus made it hard. Source documents encoded visual lines as Word
paragraphs, so DOCX conversion lost the difference between prose, dialogue,
verse, stanza breaks. Pandoc, LibreOffice, and Google Docs are useful tools here, 
but none of them alone could recover the library's reading structure.
Because the all-breaks-are-paragraphs encoding is lossy, restoration requires
intent detection trained on rendered DOCX and a bit of human adjudication.

The import pipeline is a bit compiler-shaped (frontend -> IR -> backend):

```txt
DOCX
  -> parse OOXML + Pandoc AST (DOCX frontend)
  -> typed IR for blocks and their lines
  -> passes: filter, normalize, lineate and enrich using distilled models
  -> lowering to canonical Markdown + assets (Markdown backend)
  -> scoped WritePlan
  -> content writer
```

It reads Pandoc AST where it is strong and OOXML where Pandoc drops
information.

The lineation model is a learned compiler pass. Its unit is a line 
record with source identity, render physics, source context, and
feature schema.  At runtime, the pass uses a student model to predict
lineation intent.

Student training starts with a seed round: bootstrapping labels from corpus
lines, LLM panel disagreement, and human adjudication on contested lines. 
After a seed student exists, training becomes a pool-based active-learning loop.
Early student uncertainty negatively correlated with LLM-panel disagreement.
After more labels, student uncertainty estimation was enough for
finding hard cases, then selected lines go back to privileged review, and newly
committed labels retrain the next student.

The teacher side can use privileged evidence: DOCX page renders, source
listings, context windows, LLM panel votes with adaptive reps, and human
adjudication of disagreed lines. Those signals are unavailable to student
model.

The student model is a small interpretable classifier over line features, 
evaluated with book-grouped splits, plus run-level smoothing to reduce
in-block classification errors.

Funny enough, the student now detects lineation intent better than the LLM
panel on book-grouped held-out evaluation set. The panel was most valuable
in reducing human annotation work by auto-labeling low disagreement cases.
Panel bootstrappin starts with seed LLMs, then cost-optimize the panel
while keeping comparable quality, and benchmark prompt variants.

## Agent Governance

This monorepo assumes agents will keep editing it.

Generic tools catch generic errors: TS and Astro checks, ESLint, Stylelint,
Ruff, ty, pytest, Node tests, Playwright. Pancratius also has a custom audit
harness for high-level facts like module ownership and low-level checks like
crawling all inks to check they are alive and CSS duplication drift prevention.

Audit rules are executable contracts with fixtures and self-tests, so an agent is
unlikely to silently cross a boundary without feedback.

## Corpus Intelligence

Conceptosphere is the library's self-portrait: what concepts it is made of and
how books relate to each other. Technically, it's graph-based corpus analytics tool.

- Russian text is lemmatized and filtered into a concept vocabulary.
- Concept edges use co-occurrence with NPMI pruning.
- PageRank sizes concepts and Leiden finds communities.
- Books become TF-IDF vectors over key concepts.

The static book recommendation is based on conceptual overlap and
semantic similarity.

Semantic similarity uses Qwen3 embeddings with mean-centering so a coherent
single-author corpus does not collapse into "everything resembles
everything."

## Repository Map

```txt
src/content/   corpus content, DOCX sources, downloads, and work assets
src/           Astro routes, components, styles, and site libraries
pancratius/    Python CLI for import, rendering, DOCX tools, YouTube sync, graph data, ML
build/         deterministic site build helpers run by npm
audit/         project-specific architecture, code, and content guardrails
tests/         Python, unit, browser, visual, and tooling tests
data/          committed graph/recommendation build inputs
docs/          target contracts for architecture, content, tooling, and data
```

`docs/scratchpad/` is working material, not an authoritative contract.

## Running It

```sh
npm ci
uv sync --frozen
npm run dev
```

Before claiming a change is ready:

```sh
npm run verify
```

Useful entry points:

```sh
npm run check
npm run build
npm run preview
uv run pancratius --help
```

## Documentation

- `docs/architecture.md` — site shape, stack, deploy, ownership boundaries.
- `docs/content-model.md` — books, poems, messages, pages, projects, videos, assets,
  lineation, downloads.
- `docs/tooling.md` — command surfaces and local/CI ownership.
- `docs/import-pipeline.md` — compiler-shaped DOCX import and WritePlan boundary.
- `docs/audit-harness.md` — custom project audits and rule design.
- `docs/conceptosphere.md` — concept graph, book graph, embeddings, recommender.
- `docs/downloads.md` — rendered editions and archive generation.
- `docs/i18n-routing.md` — locale route existence and fallback rules.

## License

The corpus content and derived content artifacts are dedicated to the public
domain under CC0 1.0 Universal. The site code, tooling, configuration, and tests
are MIT licensed. See `LICENSE`.
