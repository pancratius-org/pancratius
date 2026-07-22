# Contributing

Pancratius is a public-domain library of Sergey Orekhov's spiritual writings — a
static Astro site plus the Python tooling that builds it. Corpus content is
CC0; the site and tooling code are MIT.

## Command ownership

A change's owner is decided by its effect:

- `mise --locked run …` owns pure repository workflows across the site, Python,
  audits, and research projects.
- `uv run pancratius …` changes the library — import DOCX, scaffold pages, render
  downloads, regenerate committed data. Local only; never run in CI.
- `npm run …` implements Astro/TypeScript site leaves. npm and uv keep dependency
  ownership even when mise composes their commands.

Read the contract that owns your change before writing code:
[`architecture.md`](docs/architecture.md) (boundaries, stack, deploy),
[`tooling.md`](docs/tooling.md) (commands), [`content-model.md`](docs/content-model.md)
(content shape), [`i18n-routing.md`](docs/i18n-routing.md) (URLs and locales).

## Setup

Install [mise](https://mise.jdx.dev/), review `mise.toml`, then trust the
repository configuration and bootstrap the locked toolchain and dependencies:

```sh
mise trust
mise --locked bootstrap --yes
```

Tasks receive the locked environment without shell activation; use
`mise --locked exec -- <command>` for native npm or uv commands. See mise's
[`trust`](https://mise.jdx.dev/cli/trust.html) and
[activation](https://mise.jdx.dev/getting-started.html) guidance.

## The gate

One command, identical locally and in CI — green before a PR merges:

```sh
mise --locked run verify
```

Use `mise --locked run check` as the faster inner loop. Renderer and DOCX
translation changes also require `mise --locked run verify:toolchain`.

## Git

- Branch off `main`; never commit to `main` directly.
- Commit subjects are single-line, conventional: `type(scope): subject` (e.g.
  `fix(audit): …`). Scope names the subsystem you touched — `site`, `styles`,
  `layout`, `content`, `audit`, `import`, `cli`, `tooling`, `docx`, `python`,
  `ci`, `conceptosphere`, `projects`, `video`, `intent-ai`, `publication`;
  add one when a subsystem earns clear ownership.
- Open a PR; once `verify` passes, squash- or rebase-merge — squash when the
  PR's history is iterative, rebase to preserve a clean series. Either keeps
  `main` linear; no merge commits.
- Describe the change, not the journey: no commit SHAs, no "tests pass", no
  tool-generated footers.

## License

Contributions to the corpus are released under CC0; code under MIT.
