# Pancratius Open Library

Understand the root intent prior to any actions. Before changing code, data, commands, or CI, do a quick RCA: what is the real goal, what owns the work, and would the change respect boundaries or add a workaround?

Do not just grep-and-patch the first matching file; if you cannot place the work, classify it against the contracts.

## Shape

Two entry points:
- **Library:** `uv run pancratius ...`
  Local Python tooling that changes the library: import works, scaffold project pages drafts, build downloads, optimize docx, generate committed graph and embedding data for recommendations. It is not the site build and not CI. Never use bare python/pip/venv, only `uv`.
- **Site:** `npm run ...` and GitHub actions
  Astro/TypeScript tooling that builds, checks, previews, audits, and deploys the site from committed source. It may derive build artifcats, but it does not create or edit the library.

Each file has one home:
- `src/content/` — authored or imported library and site content;
- `src/` — Astro routes, components, styles, and site code;
- `pancratius/` — Python library-management package and CLI;
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

Work inside the owning boundaries. Do not change component boundaries or introduce new bridges between them, unless the task explicitly asks to re-architect them. Verify/audit before claiming done.
