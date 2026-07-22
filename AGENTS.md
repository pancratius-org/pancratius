# Pancratius Open Library

Understand the root intent prior to any actions. Before changing code, data, commands, or CI, do a quick RCA: what is the real goal, what owns the work, and would the change respect boundaries or add a workaround?

Do not just grep-and-patch the first matching file; if you cannot place the work, classify it against the contracts.

## Shape

One repository task surface delegates to two native owners:
- **Repository:** `mise --locked run ...`
  Pure development, build, check, audit, and test workflows. Mise owns the
  cross-language task graph and executable environment; it does not resolve npm
  or Python dependencies and does not mutate the library.
- **Library:** `uv run pancratius ...`
  Local Python tooling that changes the library: import works, scaffold project pages drafts, build downloads, optimize docx, generate committed graph and embedding data for recommendations. It is not the site build and not CI. Never use bare python/pip/venv, only `uv`.
- **Site:** `npm run ...`
  Astro/TypeScript implementation commands and dependencies. Mise may compose
  these leaves into repository workflows; npm does not own Python or repository
  orchestration. Site commands may derive build artifacts, but never edit the library.

Each file has one home:
- `src/content/` — authored or imported library and site content;
- `src/` — Astro routes, components, styles, and site code;
- `pancratius/` — Python library-management package and CLI;
- `intent-ai/` — downstream lineation research, annotation truth, and reproducible model evidence;
- `build/` — site build helpers run by npm;
- `audit/` — quality harness and rules;
- `tests/` — Python, unit, e2e, and visual tests;
- `docs/` — description of the target architecture (not implementation details, status, or changelog);
- `docs/scratchpad/` — transient notes and plans, not authoritative.

## Contracts

Classify the work and read the contract that owns it:
- content shape, books, poems, projects, pages, assets -> `docs/content-model.md`;
- commands, local library operations, site operations -> `docs/tooling.md`;
- code structure, boundaries, tech stack, deploy -> `docs/architecture.md`.

Work inside the owning boundaries. Do not change component boundaries or introduce new bridges between them, unless the task explicitly asks to re-architect them.

Verify before claiming done. The quality gate is `mise --locked run verify` — the full gate (checks, audits, build, Playwright e2e), run identically locally and in CI. When checks fail, diagnose the cause and improve the code or contract; do not silence or route around failures just to pass.
