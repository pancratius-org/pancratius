# Contributing

Pancratius is a public-domain library of Sergey Orekhov's spiritual writings — a
static Astro site plus the Python tooling that builds it. Corpus content is
CC0; the site and tooling code are MIT.

## Two command surfaces

A change's owner is decided by its effect:

- `uv run pancratius …` changes the library — import DOCX, scaffold pages, render
  downloads, regenerate committed data. Local only; never run in CI.
- `npm run …` builds, checks, audits, and deploys the site from committed source.
  It never creates or edits library content.

Read the contract that owns your change before writing code:
[`architecture.md`](docs/architecture.md) (boundaries, stack, deploy),
[`tooling.md`](docs/tooling.md) (commands), [`content-model.md`](docs/content-model.md)
(content shape), [`i18n-routing.md`](docs/i18n-routing.md) (URLs and locales).

## Setup

- Node — version in [`.nvmrc`](.nvmrc); `npm ci`.
- Python 3.13 via [`uv`](https://docs.astral.sh/uv/); `uv sync`.

## The gate

One command, identical locally and in CI — green before a PR merges:

```sh
npm run verify
```

`npm run check` is the faster inner loop — the same TS, Astro, Python, lint, and
test checks, minus the audits and the build.

## Git

- Branch off `main`; never commit to `main` directly.
- Commit subjects are single-line, conventional: `type(scope): subject` (e.g.
  `fix(audit): …`). Scope names the subsystem you touched — `site`, `styles`,
  `layout`, `content`, `audit`, `import`, `cli`, `tooling`, `docx`, `python`,
  `ci`, `conceptosphere`, `projects`, `video`, `lineation-core`, `publication`;
  add one when a subsystem earns clear ownership.
- Open a PR; once `verify` passes, squash- or rebase-merge — squash when the
  PR's history is iterative, rebase to preserve a clean series. Either keeps
  `main` linear; no merge commits.
- Describe the change, not the journey: no commit SHAs, no "tests pass", no
  tool-generated footers.

## License

Contributions to the corpus are released under CC0; code under MIT.
