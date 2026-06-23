# Pancratius Audit Harness

The audit harness checks Pancratius-specific contracts. It is not a generic
linter and not a second implementation of the app.

Generic tools catch syntax, formatting, and type issues. The audit exists for
facts only this project knows: source ownership, locale routing, corpus
identity, static deploy behavior, generated-vs-authored boundaries, downloads,
assets, and the split between library work and site work.

## Commands

| Command | Scope |
| --- | --- |
| `npm run audit:repo` | Core fatal rules. This is the normal repo audit gate. |
| `npm run audit:agent` | Core rules plus non-blocking heuristic checks. |
| `npm run audit:post-build` | Rules that need an emitted `dist/`. |
| `npm run audit:selftest` | Known-good/known-bad fixtures for the harness. |

Only `fatal` findings fail the command. `warning` and `info` findings are still
real review signal; they just do not block the build by default.

## What Belongs In Audit

Audit rules should enforce durable product facts:

- CI builds and publishes the static site. It does not import works, render
  release artifacts, optimize DOCX, or regenerate embeddings — those are local
  library work. The narrow exception is light external-metadata ingestion (e.g.
  `pancratius video sync`), which is additive and idempotent.
- Source content lives in `src/content/`; build input data lives in `data/`;
  public static inputs live in `public/`; deploy output lives in `dist/`.
- Books and poems are corpus works. Projects are themed sections. Pages are
  individual routes.
- A localized route/download/feed/sitemap URL exists only when that localized
  content exists.
- Public URLs, asset URLs, canonical URLs, and download URLs must work on every
  deployed host.
- Authored assets stay with the content they belong to.
- Registry facts such as locales, kind segments, work kinds, and download formats
  come from their owning source of truth.

Audit rules should not hard-fail subjective or volatile facts:

- exact visual treatment;
- prose quality or theological judgment;
- current corpus counts;
- graph ordering;
- local component names;
- CSS choices that do not break a contract.

The rule of thumb: fail on broken ownership, identity, deployability, or data
loss. Warn or inform on style, duplication, and cohesion pressure.

## Rule Design

Rules must not become another source of truth.

- **Derive, do not restate.** Read the registry, schema, helper, config, or build
  output being guarded. Do not hardcode a private copy of locale codes, kind
  names, URL segments, or download formats.
- **Make fatal rules deterministic.** A fatal rule must identify a real invalid
  state. If it can false-positive on a reasonable repo state, keep it as warning
  or info.
- **Add rules incident-first.** The best rules encode a bug, near miss, or clear
  architecture boundary. Avoid speculative cleverness.
- **Keep heuristics non-blocking.** Dead-code inventories, literal scans, docs
  drift, CSS duplication, and cohesion checks are useful review prompts, not CI
  gates.
- **Self-test gates.** Core and post-build rules need a known-bad and known-good
  fixture, or focused tests that prove the same polarity.

## Finding Shape

A finding should teach the boundary, not just name the file. Include:

- stable rule id;
- severity;
- category;
- file and line when possible;
- observed fact;
- contract;
- why it matters;
- repair;
- what not to do.

The "do not fix by" field matters. Many regressions start as a local patch that
makes one audit pass while preserving the wrong model.

## Severities

| Severity | Meaning |
| --- | --- |
| `fatal` | A durable contract is broken. The build, deploy, corpus, or source-of-truth boundary cannot be trusted. |
| `warning` | The change is probably drifting or brittle, but may be a deliberate exception. |
| `info` | A smell, duplicate, stale note, or design prompt useful to agents and reviewers. |

Fatal rules need a concrete harm. "Looks wrong" is not enough.

## Tiers

| Tier | Runs In | Use For |
| --- | --- | --- |
| `core` | `audit`, `audit:agent` | Fast source-tree invariants that should gate normal work. |
| `post-build` | `audit:post-build` | Rules that need an emitted `dist/` (PAN014 link crawl, PAN008 public-Markdown asset scan). |
| `heuristic` | `audit:agent` | Non-blocking content, cohesion, and review prompts. |

The PR audit should stay fast and deterministic. Expensive full-build crawls,
Pagefind inspection, and large archive validation belong in post-build or
scheduled checks unless the current change touched that surface.

## Current Rule Families

The canonical rule list is code: `audit/rules/index.ts`. The families below are
the architecture contracts those rules cover.

| Family | Contract |
| --- | --- |
| PAN001 | No machine-local, retired-source, or out-of-project paths in production surfaces. |
| PAN002 | Locale route existence is based on authored locale content, not fallback display data. |
| PAN003 | Locale and kind-segment sources of truth stay in sync across TypeScript and Python. |
| PAN004 | Projects do not enter work-only machinery such as bulk work archives or duplicate work identity. |
| PAN007 | Markdown asset references resolve to owned content assets. |
| PAN008 | Public Markdown exports use public asset URLs, not local or relative source paths. |
| PAN012 | CI does not install or run library-management tooling or converter/IR/writer internals. |
| PAN014 | Emitted internal links resolve in `dist/`. |
| PAN016 | Production source follows the declared TS/Python stack boundaries. |
| PAN017 | `pancratius work import --kind` derives from `CORPUS_WORK_KINDS`; projects are not work kinds. |
| PAN018 | Modules marked `# import-pure: no filesystem mutation` do not mutate the filesystem. |
| PAN019 | The `pancratius` CLI exposes no site build/verify verbs. |
| PAN021 | Every conceptosphere stable id (concept_id, community key) has an EN translation. |
| PAN022 | Every `/en/` book-reference context with a Cyrillic (RU-only) title carries the shared "Russian original" badge; `/ru/` carries zero badges. |
| PAN023 | Heuristic agent review for new raw primitives, open registries, primitive tuple contracts, and optionality clusters where domain types should carry repo vocabulary. |
| PAN024 | The `pancratius` CLI uses positional typed selectors or source-first `--to` rather than primary-target flags. |

Content-quality checks are heuristic unless promoted with a deterministic
contract and polarity coverage.

## Surfaces

| Surface | Audit Posture |
| --- | --- |
| `src/content/` | Fatal for broken ownership, missing assets, bad route/download existence; warning/info for markup inventories. |
| `src/pages/` | Fatal for fallback route bugs; warning for bypassed URL, SEO, and pairing helpers. |
| `src/lib/` | Fatal for duplicate registries; warning/info for boundary bypasses. |
| `src/components/` | Warning/info when components start deciding corpus identity or download existence. |
| `pancratius/` | Fatal for CI-boundary violations, destructive defaults, or mutation outside the writer boundary. |
| `build/` | Fatal if build derivations mutate source or shell into corpus tooling to manufacture source. |
| `audit/` | Fatal if a gating rule loses polarity coverage or restates facts it should derive. |
| `data/` and `public/` | Fatal for stale, leaked, or unpublishable payloads. |
| `docs/` | Warning/info for stale contracts unless a doc is machine input. |

## Tests vs Audits

Tests run code and prove behavior on inputs. Audits read the tree or emitted
artifact and prove a structural invariant. Do not replace one with the other.

Examples:

- Verse conversion behavior belongs in pytest and source-fidelity audits that
  inspect the DOCX source.
- "Only the writer mutates `src/content/`" belongs in audit, because it is a
  source-tree shape invariant.
- "CI never runs import/render tooling" belongs in audit, because it is a
  workflow contract.

## Implementation Constraints

Keep the harness small and explicit.

- The rule registry is an explicit list in `audit/rules/index.ts`.
- TS rules own rule identity, severity, category, and finding text.
- Python checks are subprocesses for checks that need Python parsers or the
  Python package.
- Fixture trees live under `audit/fixtures/` and are excluded from normal type
  and lint scope because they are test data.
- Rules should return findings, not print private reports or mutate the repo.
