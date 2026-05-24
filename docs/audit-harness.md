# Pancratius Audit Harness

This document defines the shape of repo-specific audits for Pancratius. It is a
design spec, not an implementation checklist. The goal is to make it difficult
for an agent to violate the architecture accidentally, and easy for that agent
to understand what it did wrong.

The harness should not become a generic linter farm. Generic lint tools can
catch syntax, formatting, and type errors. This harness exists for Pancratius
contracts: source ownership, static deploy behavior, locale routing, corpus
identity, downloads, assets, and the difference between authored content and
generated artifacts.

## Purpose

The audit harness has three jobs:

1. Fail the build when a change violates a durable product or architecture
   contract.
2. Warn when a change looks likely to become drift, even if the site still
   builds.
3. Inform future agents about duplication, over-decohesion, stale docs, and
   local smells before those smells become architecture.

An informational finding is still useful. It does not fail the agent, but it
should make the agent stop and think: "This is probably not cool; maybe this
should be a shared component, a helper, or an explicit exception."

## First principles

Audits should encode real invariants, not current accidents.

Durable things worth enforcing:

- Pancratius is a static site. The deploy artifact is `dist/`; the host serves
  files.
- CI builds and publishes the site. It does not import DOCX, render PDF/EPUB,
  optimize DOCX, or regenerate embeddings.
- Source content lives in `src/content/`. Build input data lives in `data/`.
  Public static inputs live in `public/`. Build output lives in `dist/`.
- A book or poem is a corpus work, paired across languages by `(kind, number)`.
- A project is a themed section, not a downloadable work.
- A page is an individual route, not a member of a generic page population.
- A localized URL exists only when that localized content exists.
- Display fallback is allowed; route/download existence fallback is not.
- Public URLs, canonical URLs, downloads, and asset URLs must respect the
  deploy target and mirror/base path.
- Authored assets stay with the content they belong to.

Transient things not worth hard-failing:

- Exact component names and file splits.
- Exact visual treatment of a page or project.
- Exact copy quality, theology, or marketing strength.
- Exact graph ordering or recommendation weight.
- Exact CSS declarations when they are presentation choices.
- The current number of books, poems, pages, or projects.

The rule is: fail on broken ownership, identity, deployability, or data loss.
Warn or inform on style, duplication, and cohesion pressure.

## Harness Principles

The harness must not become another source of truth.

1. **Derive, do not restate.** A rule should read the registry, schema, or helper
   it is guarding. It should not hardcode its own copy of locale codes, kind
   names, URL segments, download formats, or route grammar. When duplication is
   unavoidable across TypeScript and Python, the duplication must be explicit
   and guarded by a parity audit.
2. **Fatal rules must be deterministic.** If a legitimate change can make a
   fatal rule false-positive, the rule is not ready to be fatal. Demote it to
   warning or info until it checks an observable consequence rather than a
   guess.
3. **Heuristics stay non-blocking.** Literal-text inventories, CSS duplication,
   dead-code reachability, docs drift, and cohesion checks are useful because
   they inform agents. They should not gate CI unless they prove a hard contract
   violation.
4. **Add rules incident-first.** A rule is strongest when it encodes a real bug
   or near miss: a fallback locale route, stale generated path, hardcoded corpus
   count, converter overwrite risk, broken download link, or orphaned artifact.
   Do not build speculative cleverness just because it is detectable.
5. **Self-test the harness.** A rotted audit is false confidence. Each rule that
   gates CI should have a known-bad fixture or focused test proving that it
   fires when its contract is broken.

## Severity

Use three severities.

| Severity | Meaning | CI behavior |
| --- | --- | --- |
| `fatal` | A durable contract is broken. The build, release, mirror, corpus, or source-of-truth boundary is untrustworthy. | Exit non-zero in CI. |
| `warning` | A change is probably drifting or brittle, but may be a deliberate local exception. | Do not fail by default; summarize prominently. |
| `info` | A smell, duplication cluster, stale comment, or design prompt useful to agents and reviewers. | Never fail; report in a readable inventory. |

Fatal rules must be narrow and defensible. If a rule cannot explain the user or
deployment harm, it should not be fatal.

A fatal rule also needs a false-positive gate: if the implementation can flag a
valid repo state, the rule must be non-fatal until it is made more precise. For
example, a visible-literal scan cannot be fatal merely because it finds English
text; it can be fatal only if it proves that the wrong language renders under a
localized route or that generated metadata disagrees with route metadata.

## Finding Shape

The output should be optimized for both humans and agents. JSON may be useful
for tool integration, but it is not the product. The product is a clear finding
that teaches the boundary.

Each finding should contain:

- stable rule id, for example `PAN002`;
- severity;
- category;
- file and line when possible;
- observed fact;
- violated contract or architectural pressure;
- why it matters;
- suggested repair;
- what not to do as a "fix";
- whether the finding is fatal, warning, or informational.

Example:

```txt
PAN001 fatal path-boundary
src/content/projects/enlightened-ai/ru.md:42

Observed: image reference points to legacy/projects/foo.png.
Contract: production source may reference project-owned source assets,
public URLs, or generated public asset endpoints, but not retired source trees
or machine-local paths.
Why it matters: a mirror, clean clone, or CI build cannot depend on retired
working material.
Repair: move the asset into the project bundle or into an explicit public/static
asset location, then reference it through the approved URL helper.
Do not fix by: adding another regex rewrite in the renderer.
```

The "do not fix by" line is important. Many architecture regressions start as a
local patch that makes one audit pass while preserving the wrong model.

## Architecture Surfaces

| Surface | Durable Truth | Common Drift | Audit Posture |
| --- | --- | --- | --- |
| `src/content/**` | Authored source content and committed release artifacts live with the work/page/project they belong to. | Generated files treated as source; project resources treated as books; external machine paths leak into Markdown. | Fatal for broken ownership and missing assets; info for markup inventories. |
| `src/pages/**` | Routes are explicit static routes. A locale route exists only for authored locale content. | Route code duplicates pairing, SEO, URL logic, visible copy, or links; fallback content leaks under `/en/`. | Fatal for fallback/existence bugs; warning for duplicated routing logic, hardcoded copy, and bypassed URL helpers. |
| `src/lib/**` | Domain rules live here: locale, kind/segment, work pairing, projects, downloads, SEO, paths. | New helpers encode a second truth; route files bypass helpers. | Fatal for duplicate SSOT; warning/info for boundary bypasses. |
| `src/components/**` | Components compose rendering. They do not decide corpus identity or download existence. | Components learn domain rules; one-off pages become generic dispatchers. | Warning for coupling; info for extraction opportunities. |
| `src/styles/**` and component CSS | CSS expresses presentation over explicit markup. It should not infer source semantics. | Copied decorations, orphaned stylesheets/selectors, specificity creep, global page-specific selectors. | Info first; warning for repeated large blocks, unreachable CSS, or semantic heuristics. |
| `scripts/**` | Local/admin scripts import, render artifacts, prepare data, and audit. Build scripts must respect source ownership. | Destructive defaults; orphaned admin scripts; Python/TS constants drift; CI runs local library work. | Fatal for destructive or CI-boundary issues; warning/info for duplicate constants and unreachable scripts. |
| `data/**` | Build input data, not public web content. | Published caches, stale graph payloads, source paths used as runtime links. | Fatal for public leakage and stale required payloads. |
| `public/**` | Stable unprocessed public files only. | Renderable assets placed here to dodge Astro hashed URLs; stale graph/data files. | Fatal for broken public payloads; warning for pipeline bypasses. |
| `.cache/**` | Disposable generated cache. | Cache treated as source or committed durable truth. | Fatal if source depends on cache-only paths. |
| `dist/**` | Disposable deploy output. | Scripts read `dist/` as source of truth; internal links, sitemap, feed, or search drift from emitted pages. | Fatal if source/build logic depends on previous `dist/`, or if emitted navigation surfaces are broken. |
| `docs/**` | Architecture memory and human contracts. | Docs describe old behavior after refactors. | Warning/info, unless docs are used as machine inputs. |
| CI/package scripts | Validate, build, index, publish. | CI imports/renders/optimizes the library. | Fatal for import/render/build separation violations. |

## Rule Families

### PAN001: Path Boundary

No source, generated public payload, route metadata, or download output should
depend on an out-of-project or retired-source path.

Allowed path classes:

- project-relative source paths under approved source roots such as `src/`,
  `data/`, `public/`, and `.cache/` when used for their intended phase;
- emitted public URLs such as `/assets/...` or localized route URLs;
- external `https://` URLs when they are actual reader-facing references.

Disallowed path classes:

- machine-local paths such as `/Users/...`, `~/...`, `C:\...`;
- parent traversal that escapes the repo or bundle, such as `../../MyWorks`;
- retired source trees such as `legacy/...` in production source or public
  payloads;
- absolute filesystem paths embedded in Markdown, JSON payloads, sitemap,
  public Markdown, TXT, HTML, or generated archives.

`legacy/` is not special because of its name. It is special because it is not
part of the production source model. If a still-needed asset or source text
lives there, the repair is to import or move it into the correct production
bundle. Do not teach renderers to reach back into `legacy/`.

### PAN002: Locale Route Existence

A localized HTML route, download route, feed entry, sitemap URL, or hreflang
alternate exists only when that localized entry exists.

Display fallback may be used for secondary display data: cover fallback,
cross-reference titles, JSON-LD display, and similar small derived surfaces.
Route existence must read the locale entry directly.

Fatal examples:

- `/en/books/foo/` renders the Russian body because the EN entry is missing.
- `/en/projects/foo/` exists because the RU project exists.
- a sitemap advertises an alternate URL that no static route emits.

### PAN003: Single Sources Of Truth

The harness should guard repo-level registries:

- locale list and default locale;
- locale metadata and fallback chain;
- kind -> URL segment mapping;
- work-pair kinds versus broader URL kinds;
- route helper grammar;
- download formats;
- public asset endpoint grammar.

Some duplication is unavoidable across TypeScript and Python. When it is
unavoidable, there must be one owning source per language plus a cross-language
audit. Do not scatter un-audited copies through route files and scripts.

This also covers derived facts. Corpus counts, archive sizes, graph counts,
route counts, and search document counts should be computed from the source or
generated manifest that owns them. Hardcoded user-facing counts such as "72
books" are warning-level by default, and fatal only when they create an emitted
contract mismatch such as sitemap/feed/search metadata lying about what was
built.

### PAN004: Work/Project Boundary

Books and poems are corpus works. Projects are themed sections.

Fatal examples:

- project entries flow through work-pair machinery;
- projects appear in per-work PDF/EPUB/DOCX download routes;
- projects appear in `all-md.zip` as ordinary works;
- converters overwrite authored project pages;
- project resources require `(kind, number)` work identity unless they are
  deliberately promoted to real books or poems.

Rendering components are not identity. A project resource may reuse a book-like
reader component without becoming a book.

### PAN005: Generated/Authored Ownership

Audits should know which files are authored, generated, release artifacts, and
build output.

Fatal examples:

- a build step mutates authored Markdown;
- `--clean` deletes an entire content kind instead of selected generated files;
- generated cache is required for a clean clone to build;
- committed release artifacts are silently regenerated in CI;
- public Markdown is generated by a second renderer that disagrees with the
  canonical public Markdown renderer.

### PAN006: URL And Mirror Contract

Canonical URLs, Open Graph URLs, sitemap URLs, download links, archive links,
and image links must be produced by approved helpers or endpoints that understand
locale and deploy target.

Fatal examples:

- a hardcoded production domain in generated metadata;
- a mirror build emits primary-domain canonical URLs;
- a base-path mirror has broken absolute asset or download links;
- regex route rewrites replace a URL helper.

Hardcoded route fragments inside tests or docs may be informational. Hardcoded
runtime URL grammar in production code should be warning or fatal depending on
surface.

### PAN006B: Visible Copy And Literal Text Locality

Hardcoded visible text is not always a production bug, but it is a common source
of localization drift and editor confusion. The audit should inventory visible
string literals in Astro pages, Astro components, TypeScript UI modules, and
client scripts, then classify them by where they belong.

Allowed or usually acceptable:

- short technical tokens such as file extensions, format names, and schema
  values;
- punctuation, separators, and typographic ornaments;
- `aria-hidden` decorative glyphs;
- test fixtures and docs examples;
- one-off text inside a route that is deliberately not localized yet and is
  documented as such.

Warnings:

- user-visible Russian or English prose embedded directly in route/component
  templates instead of Markdown, page frontmatter, page-local data, or a copy
  module;
- duplicated visible labels across locale folders;
- SEO titles/descriptions hardcoded in route files when equivalent page
  frontmatter or `seo` helpers exist;
- component-specific chrome strings placed in a global dictionary only because
  the component had no local copy module;
- page editorial copy placed in a global UI copy dictionary.

Fatal examples:

- hardcoded text causes the wrong language to render under a localized route;
- a locale branch such as `locale === "en" ? ... : ...` silently makes adding a
  third language wrong;
- a hardcoded string duplicates route metadata and causes canonical, sitemap,
  Open Graph, or feed text to disagree.

The repair depends on ownership:

- page/editorial copy belongs in localized Markdown/frontmatter or page-scoped
  data;
- reusable chrome belongs near the component that consumes it, or in a small
  shared chrome dictionary when it is truly global;
- SEO should flow through shared SEO helpers and page/work metadata;
- URLs should flow through URL helpers, not copy dictionaries.

This rule should not demand a massive single translation file. Locality matters:
strings should live at the smallest stable ownership boundary.

### PAN007: Assets And Images

The audit should distinguish asset roles:

- cover source asset;
- body illustration;
- bibliography thumbnail/provenance image;
- public static file;
- generated optimized rendition;
- generated stable corpus image endpoint.

Fatal examples:

- Markdown references an image that does not resolve in dev, preview, static
  deploy, and public Markdown;
- public Markdown image URLs point to machine-local, `legacy/`, or missing
  paths;
- graph JSON or project data contains stale source asset paths;
- a bibliography thumbnail is rendered as a body illustration.

Warnings:

- renderable one-page images live in `public/` only to get a stable URL;
- large unbounded body images exceed the source asset policy;
- generated public assets duplicate many unused source images.

### PAN008: Download And Corpus Export Contract

Per-work downloads are alternate representations of a work. Bulk archives are a
separate corpus surface.

Fatal examples:

- public `.md` includes YAML frontmatter;
- public `.txt` leaks Markdown, HTML wrappers, or metadata;
- PDF visible text includes YAML metadata;
- emitted download links point to files that do not exist;
- projects leak into work archives;
- route existence uses display fallback.

Warnings:

- duplicate Markdown cleanup logic appears in two languages;
- public Markdown and TXT transformations diverge from the canonical renderer;
- archive contents change without a manifest or checksum update.

### PAN009: CSS And Presentation Drift

CSS audits should begin as informational. Presentation has legitimate local
variation, and hard-failing on repeated declarations will cause bad abstractions.

Useful information:

- identical declaration blocks repeated across components;
- repeated selector patterns such as many local heading ornaments;
- component CSS that duplicates global prose CSS;
- global selectors targeting one page or project;
- specificity growth;
- color palette drift into too many one-off tokens;
- component-scoped styles that would be clearer as a shared primitive.

Warning threshold examples:

- the same non-trivial block appears in several files;
- a component comment says it must stay in sync with a global stylesheet;
- CSS infers document semantics from visual heuristics instead of explicit
  markup, for example treating all italic short paragraphs as verse.

### PAN010: Component And Page Cohesion

The harness should detect kitchen-sink growth without dictating component taste.

Warnings or info:

- shared page schemas accumulate fields used by exactly one page;
- a generic page renderer dispatches on slugs;
- route files duplicate the same loading/layout sequence enough to justify a
  small helper;
- page-specific data moves into global copy dictionaries;
- UI/chrome copy moves into editorial Markdown.
- visible strings are centralized or scattered at the wrong ownership boundary.

Fatal only when the drift causes a product bug, such as a route rendering the
wrong locale, a project entering the work corpus, or a generated script
overwriting authored pages.

### PAN011: Documentation Drift

Docs are part of the agent interface. A stale doc can be as damaging as a stale
comment because future agents will act on it.

Warnings:

- architecture docs mention deleted routes such as a generic `[slug].astro`;
- docs describe projects as corpus works after projects have become sections;
- docs say a converter may write a path that current import scripts reject;
- command docs mention scripts or flags that no longer exist.

Docs should not fail production CI by default, but an agent-facing audit report
should surface these loudly.

### PAN012: Local Library Management Boundary

Audits should protect the import/render/build split.

Fatal examples:

- CI installs or runs pandoc, typst, embedding models, DOCX optimizers, source
  importers/renderers, or the converter/IR/writer library modules behind them
  (the DOCX adapter, the typed IR + normalize/lower, footnote/cross-ref analysis,
  the WritePlan, and the writer) — whether invoked by `.py` path or as a dotted
  module (`python -m scripts.lib.…`);
- Astro routes render PDF/EPUB/DOCX on demand;
- build scripts regenerate committed release artifacts;
- import scripts write to projects by default.

Warnings:

- scripts mix import, release rendering, and build-publish behavior in one
  command;
- script names obscure whether they mutate source or emit disposable output;
- scripts keep retired modes alive only to print "this is not supported"
  messages, instead of removing the mode from the CLI surface.

### PAN013: Orphaned And Retired Code

Dead code is not harmless in an agent-edited repository. Future agents use
local examples as training context. An unused stylesheet, client script, Python
helper, route, or component can teach the wrong architecture long after the
runtime stopped using it.

This rule should detect unreachable or unreferenced code across surfaces:

- standalone CSS files not imported by `global.css`, Astro components, or other
  approved entrypoints;
- CSS selectors that do not match any source or built HTML surface, reported
  carefully because Markdown-rendered content can create classes dynamically;
- Astro components with no imports and no route/content entrypoint;
- client scripts or public JavaScript files not referenced by any route,
  component, or public HTML surface;
- Python scripts not referenced by package scripts, docs, Make-like wrappers,
  CI, other scripts, or an explicit admin-tool index;
- retired compatibility helpers no longer called by any script or production
  code;
- stale generated artifacts whose source generator no longer exists.

Default severity should be informational. Escalate to warning when the orphan is
large, architecture-shaped, or likely to mislead agents. Escalate to fatal only
when the orphan is still shipped publicly, shadows a live asset, or contradicts
a hard architecture contract.

Examples:

- an unused CSS file defining an old project theme is `info` or `warning`;
- a public JavaScript file emitted to `dist/` but unused by any page is
  `warning`;
- a legacy importer still reachable from `package.json` and able to overwrite
  authored content is `fatal`;
- a Python helper unused except by a documented local admin command is not an
  orphan.

The repair should prefer deletion over compatibility glue, but deletion must be
evidence-based. If the audit cannot prove a file is unused, report the usage
question and point to the missing ownership marker instead of guessing.

Reachability is noisy. Do not make this rule fatal unless it proves the orphaned
surface is shipped, dangerous, or still reachable from a mutating command.

### PAN014: Built-Surface Crawl And Index Sanity

Static deploy correctness is not proven by `astro build` alone. The built site
must be crawled as a file-hosted website because many errors only appear after
routes, base paths, generated assets, sitemap, feeds, Pagefind, and downloads
are emitted together.

This audit should run after a production build, and separately for every deploy
target whose `site` or `base` differs.

Fatal examples:

- an internal HTML link points to a missing emitted page or file;
- a canonical, Open Graph, sitemap, feed, or hreflang URL points to a route that
  was not emitted;
- sitemap entries omit required readable pages or include nonexistent localized
  pages;
- RSS/feed links disagree with canonical URL helpers or include missing pages;
- Pagefind assets are absent, empty, or not loadable from the built base path;
- the search index omits major intended surfaces such as books, poems, projects,
  or static pages;
- a download link rendered in HTML is missing from `dist/`;
- a base-path mirror build emits links that only work at domain root.

Warnings or info:

- sitemap count changed significantly without an obvious content change;
- Pagefind document count changed significantly;
- feed item count changed unexpectedly;
- internal links use redirects or non-canonical slash forms;
- pages exist in `dist/` but are unreachable from navigation, sitemap, or other
  intentional entrypoints.

The crawler should respect the deployment model: plain static files served by a
host, with the target's base path applied. It should not assume a dev server's
fallback behavior or SSR semantics.

### PAN015: Retired Capability Surface

A retired capability should disappear from the public command/API surface. It
should not remain as a negative feature that says, in effect, "this is an
apple, not a pear."

This pattern usually appears as:

- CLI choices that include a value only so the code can reject it later;
- branches such as `if kind == "project": raise SystemExit(...)` after projects
  are no longer a valid import kind;
- long comments explaining why a deleted architecture is no longer allowed;
- compatibility adapters that exist only to preserve calls nobody should make;
- docs that tell users which retired mode not to use instead of presenting the
  valid command shape.

Correct repair:

- remove the retired value from CLI choices, command help, docs, and typed
  unions used by that tool;
- introduce a narrower domain type such as `ImportableKind = book | poem` when
  the broader product still has `project`;
- delete legacy loaders, branches, and schema paths for the retired mode;
- let the normal CLI/parser error explain invalid input;
- keep a short changelog or architecture note if historical context matters.

Temporary exception:

A loud refusal is acceptable as a short-lived migration guard when the old mode
can destroy authored source, especially if external callers may still invoke it.
That guard must have an owner and deletion condition. Without those, it becomes
architecture sediment.

Severity:

- `warning` when the retired surface is reachable but harmless;
- `fatal` when it still performs writes, influences defaults, appears in CI, or
  preserves a source-of-truth violation;
- `info` when only comments/docs retain the negative framing.

The audit should steer agents toward removing the invalid shape, not polishing
the refusal message.

### PAN016: Stack Conformance

The architecture declares a bounded technology surface — frameworks, source
languages, runtime dependencies, where code and markup may live. Anything outside
that surface is drift, even when the site still builds. This rule family verifies
the implementation stays inside the stack declared in `architecture.md` → "Stack"
and "Routing".

This is the policy layer, not a linter. It encodes the project-specific
*boundary* a generic tool cannot know ("this repo forbids React", "production
source is TypeScript, not JavaScript"). Generic correctness — does this file type-
check, are these annotations present — is delegated to the standard tools below;
the rule's job is to ensure those tools exist, run, and leave no source tree
uncovered.

**Derive, do not restate (PAN003 applies).** Read the declared stack from its
sources — the `architecture.md` Stack section, `package.json` dependencies, and
`tsconfig` (`allowJs: false`, `exclude`) — rather than hardcoding the banned list
in the rule. Adding a forbidden framework should trip the rule because it appears
in `package.json`/imports, not because the rule happens to name it.

Members (each an instance of the same "stay in the declared surface" idea, so the
family extends cleanly to the next banned thing):

- **Source language.** Production source is TypeScript. No handwritten JavaScript
  or JSX (`.js`, `.mjs`, `.cjs`, `.jsx`) in the production-source working tree
  (implementation walks the tree rather than `git ls-files` so it runs against a
  fixture too; equal-or-stricter than tracked — a stray untracked `.js` is also
  out of stack). Allowed only in
  declared non-production trees: `legacy/` and `design/` (excluded until deleted),
  vendored third-party output such as `public/pagefind/`, and generated/disposable
  trees (`.astro/`, `.cache/`, `dist/`, `node_modules/`). The allowlist should be
  derived from `tsconfig` `exclude` plus the doc's named exclusions, not invented
  here. **Fatal** — a deterministic git/filesystem check, the textbook small fatal
  core.
- **UI-framework boundary.** No React, Vue, Svelte, Solid, or Tailwind — neither
  as a `package.json` dependency nor imported anywhere in source. **Fatal** — a
  deterministic dependency + import scan.
- **Markup boundary.** Pages and components go through the Astro pipeline. A
  hand-authored standalone `.html` file acting as a route is out of stack.
  **Warning**, escalating to **fatal** if it shadows or competes with an emitted
  route.
- **Bundling boundary.** Client libraries are bundled via npm, not loaded from a
  CDN (the doc states the conceptosphere viz libs are "bundled via npm, not CDN").
  A `<script src="https://cdn…">` for a library that should be bundled is
  **warning**, **fatal** when a deployed page depends on third-party CDN
  availability at runtime.
- **Dependency / runtime boundary.** Runtime dependencies stay within the declared
  set; Python runs via `uv` with locked deps — no `pip install`, `conda`, or
  `requirements.txt`. A new undeclared runtime dep is **warning**; a banned
  mechanism (`requirements.txt`, a CDN runtime dependency) is **fatal**.
- **Typed source (delegated).** TypeScript must be strict; Python must be
  annotated. These are *generic* checks, so the harness does not re-implement them
  — it asserts only that the standard typecheck/lint tools are wired into CI and
  that no tracked tree escapes them. Two coverage gaps are easy to leave open: the
  app `tsconfig` excludes `scripts/` and `tests/` (they need their own typecheck),
  and Python needs annotation coverage. The standing policy is **no new untyped
  Python or untyped `.ts` without an explicit, scoped boundary comment** — and the
  goal is annotation *coverage* plus best-effort static checking, not a claim of
  total type soundness. Which tools, versions, and suppression conventions
  implement this is not contract — see `tooling.md` and `decisions.md`.

Repair: remove the out-of-stack artifact (delete the `.mjs`, drop the framework,
bundle the CDN lib, add the missing annotation), or, if a tree is legitimately
non-production, make that explicit in the derived allowlist with a reason.

Do not fix by: adding the offending path to a broad ignore list to silence the
scan; renaming a `.js` to `.ts` with no real typing just to pass the extension
check (the typecheck-coverage member is what makes that a hollow fix); or vendoring
a banned framework under `public/` to dodge the dependency scan.

Severity summary: fatal for handwritten JS in production source and for a banned-
framework dependency/import (deterministic, clear harm: the wrong stack ships or
the strict typecheck is bypassed); warning for markup/CDN/undeclared-dep drift that
may be a deliberate local exception; the annotation member rides on the delegated
tools and is as fatal as their CI configuration makes them.

### PAN017: Import Work-Kinds Guard

A concrete instance of PAN015 (retired-capability surface) and PAN003 (single
source of truth), made deterministic and fatal because it is backed by a real
incident: the converter once had project import/conversion paths and a
destructive `--clean`, and projects must never re-enter the work/import machinery
(PAN004).

"Work kinds" — the kinds the import/converter pipeline handles and which kinds get
a download matrix — has one Python source of truth: `WORK_KINDS` in
`scripts/lib/kinds.py` (`("book", "poem")`). `SEGMENT_OF` in the same module
deliberately stays broader: it keeps `project` because projects still route under
`/projects/` and appear in the sitemap. Routing breadth is not work scope.

This rule derives from those two facts and asserts:

1. the import CLI's `--kind` argparse `choices` in `scripts/import_docx.py` equals
   `WORK_KINDS` — and, when expressed as a bare name, that name is `WORK_KINDS`
   imported `from lib.kinds`, not a hardcoded list;
2. `"project" not in WORK_KINDS` — projects are themed sections, not convertible
   or downloadable works;
3. `WORK_KINDS` is a subset of `SEGMENT_OF`'s keys — every work kind still routes.

Fatal examples:

- `scripts/import_docx.py` hardcodes `--kind` `choices=("book", "poem", "project")`
  (or any literal that drifts from `WORK_KINDS`), re-admitting project as an
  importable kind so the converter could write authored project sections through
  work machinery;
- `project` is added back to `WORK_KINDS`;
- a work kind is added to `WORK_KINDS` without a `SEGMENT_OF` entry, so it has no
  URL segment.

Repair: keep `WORK_KINDS` the SoT in `scripts/lib/kinds.py` and have
`import_docx.py` use `choices=WORK_KINDS` (imported from `lib.kinds`). Promote a
kind to a work by adding it to `WORK_KINDS` (and `SEGMENT_OF`), never by
special-casing it in the CLI.

Do not fix by: hardcoding the `--kind` choices to silence the parity check, or
widening `WORK_KINDS` to make projects "fit" the import/work machinery instead of
keeping them a section.

Implemented as a Python checker (`python/import_work_kinds.py`) that imports
`kinds.py` and AST-parses the CLI's argparse, wrapped as **fatal core** by
`rules/imports.ts` (`PAN017-import-work-kinds`) via `runPythonCheck`, with
both-polarity fixtures under `fixtures/PAN017-import-work-kinds/{good,bad}/`.

### PAN018: Writer-Only Mutation

The second import-boundary rule, in the PAN005 (generated/authored ownership)
family and backed by the import-pipeline contract's *safety boundary*
(`import-pipeline.md` → "The writer — the only mutator"): import code *produces*
a `WritePlan`; only the writer (`scripts/lib/writer.py`) mutates `src/content`.
Every other import-pipeline module that helps produce the plan must be pure — no
filesystem mutation — so an adapter, normalizer, analyzer, or lowerer can never
quietly copy media into `src/content` as a parse side effect (the old shape this
redesign kills).

A module declares it is in the pure boundary with a marker comment on its first
lines:

```
# import-pure: no filesystem mutation
```

The rule **derives the scanned set from those markers** (PAN003 "derive, do not
restate"): it is a self-extending source of truth — every pure import module
carries the marker, so the DOCX adapter's pure neighbors (the typed IR, its
normalize/lower passes, footnote and cross-ref analysis, the narrow OOXML reader,
and the `WritePlan`) are all covered, and any new pure stage is covered the moment
it carries the marker, with no rule edit. Each marked module must contain **no**
filesystem-mutation call:

- attribute calls — `.write_text`, `.write_bytes`, `.mkdir`, `.touch`,
  `shutil.copy*`/`move`/`rmtree`, `os.replace`/`remove`/`rename`/`unlink`/
  `makedirs`, `Path.rename`/`replace`;
- `open(..., mode)` where the mode requests writing (`w`/`a`/`x`, incl. binary/
  plus variants).

The pure modules — `ir.py`, `ir_normalize.py`, `ir_lower.py`, `footnotes.py`,
`cross_refs.py`, `ooxml.py`, and `writeplan.py` — carry the marker. `writer.py`
deliberately does **not** — it is the designated mutator, the one place mutation
is allowed to live, so it is never scanned; nor do the impure boundary modules
that legitimately touch the outside world (`docx_adapter.py` shells to pandoc and
reads the source zip, `docx_conversion.py` stages converter output). The detection
lives in a Python checker (it tokenizes for a real COMMENT marker — a docstring
mention does not count — and AST-walks the module); the marker requirement also
fails loud if the SoT vanishes (no marked module ⇒ FAIL).

Fatal examples:

- `writeplan.py` (or any future marked module) gains a `.write_text` /
  `shutil.copyfile` / `open(p, "w")` into a bundle;
- a new pure stage carries the marker but copies an extracted image into the work
  folder directly instead of returning a `copy` `WriteOp`.

Repair: move the mutation into `scripts/lib/writer.py` and have the pure module
return a `WritePlan`/`WriteOp` describing the intended write. If a module
genuinely must mutate, it is not pure — remove its marker and route its writes
through the writer.

Do not fix by: deleting the marker to silence the scan while keeping the write,
or special-casing the call so the AST check misses it.

Implemented as a Python checker (`python/writer_only_mutation.py`), wrapped as
**fatal core** by `rules/imports.ts` (`PAN018-writer-only-mutation`) via
`runPythonCheck`, with both-polarity fixtures under
`fixtures/PAN018-writer-only-mutation/{good,bad}/` (good = a marked module with
no mutation; bad = a marked module containing a `.write_text`).

## Surface-Specific Implementation Guidance

### TypeScript and Astro

Use AST-aware checks where the false-positive cost is high:

- imports from `astro:content`;
- imports from `src/lib/works`;
- calls to `entryForLocale`;
- hardcoded URL strings in production route/component code;
- visible string literals in templates, JSX-like expressions, and client
  scripts;
- duplicate locale/kind lists;
- route `getStaticPaths` logic;
- import graph reachability for components, utilities, and client scripts.

Regex scans are acceptable for low-risk inventories, but fatal findings should
avoid brittle matching when a parser is reasonable.

### Python

Python scripts are allowed to be practical, but the audit should guard their
write boundaries.

Check:

- which directories a script can write;
- whether `--clean` can delete authored source;
- whether a retired mode has disappeared from importer/converter CLIs instead
  of surviving as a refusal branch;
- whether Python copies locale/kind constants from the TS side and whether an
  audit keeps them aligned;
- whether regex Markdown rewrites are compensating for a source model bug;
- whether scripts are reachable from package scripts, CI, docs, other scripts,
  or an explicit admin-tool inventory.

### Markdown And Content

Content audits should be strict about references and gentle about style.

Fatal:

- invalid frontmatter;
- missing default-locale work entry;
- duplicate `(kind, number, lang)`;
- broken relative assets;
- machine-local or retired-source paths;
- impossible cross references.

Info:

- raw HTML inventory;
- repeated blocks across pages;
- untranslated title or placeholder wording;
- TODO descriptions;
- literal footnote markers without definitions.

### CSS

Use PostCSS or an equivalent parser for CSS inventories.

Do not fail merely because two components share a declaration. Instead, group
duplication by useful clusters:

- exact selector duplicated;
- exact declaration block duplicated;
- near-identical blocks with one token changed;
- global style duplicated by component style;
- repeated ornament/pattern code;
- stylesheet and selector reachability, with conservative handling for classes
  emitted from Markdown, converters, or generated HTML.

The report should suggest likely extraction targets, not auto-extract them.

### Generated And Built Surfaces

Some audits must inspect emitted files because static deploy correctness is
observable only after build:

- sitemap;
- RSS/feed;
- Pagefind output when search is involved;
- `dist/**/*.html` links;
- emitted downloads;
- `all-md.zip`;
- public graph JSON;
- public Markdown image URLs.
- complete internal link crawl;
- sitemap/feed canonical parity;
- Pagefind asset and index sanity.

These checks should never treat `dist/` as source. They validate the build
artifact and then discard it.

## CLI Shape

The CLI should be quiet, readable, and deliberate.

Commands (implemented):

```txt
npm run audit                 # fast deterministic fatal core — the PR-CI gate
npm run audit:agent           # core + non-blocking heuristics, grouped, all severities
npm run audit:deploy          # post-build crawl/index checks (needs dist/) — deploy gate
npm run audit:selftest        # both-polarity fixture self-test of every gating rule
```

`audit` is the canonical surface (a site-door verb — see `tooling.md`); a TS
engine (`harness.ts`) with Python checks subprocessed via `python.ts`. Mode is a
positional arg (`harness.ts [agent|deploy]`). Category-filtered modes
(`audit:styles` / `audit:content` / `audit:docs`) are a future convenience to add
once the CSS/cohesion/docs families exist — they would filter the registry by
category, not introduce new behavior.

The exact command names are less important than their behavior:

- fatal CI output should be short and actionable;
- agent output should be richer and grouped by architectural boundary;
- informational reports should not drown fatal failures;
- each rule should have a stable id;
- each rule should have a short rationale in docs;
- no audit should rewrite files unless it is explicitly a separate fixer.

The default PR audit should stay fast and deterministic. Expensive built-surface
checks such as both-target builds, complete crawls, Pagefind inspection, and
large archive validation belong in deploy workflows or scheduled/nightly jobs
unless the current change touched the affected surface.

Avoid over-engineering the report transport. Human-readable text is mandatory.
JSON or NDJSON is useful only if another script consumes it. If machine output
is added, keep it a secondary representation of the same findings.

## Implementation Shape

The implementation should be simple enough that a future agent can inspect one
rule, understand it, and safely add another. Avoid a clever plugin framework.

Shape (as built; `[planned]` = the family is specified above but not yet
implemented — add it incident-first):

```txt
scripts/audit/
  harness.ts              # CLI entrypoint: npm run audit [agent|deploy]
  selftest.ts             # both-polarity fixture runner (npm run audit:selftest)
  lib/
    finding.ts            # severity, category, rule id, location, message
    rule.ts               # Rule + RuleContext + makeContext (the rule contract)
    repo.ts               # repo root, walk/read with the ignore + fixtures-exclusion policy
    report.ts             # pretty report; CI-terse vs agent-grouped
    text.ts               # line numbers, snippets, regex-with-lines
    python.ts             # the Python-subprocess normalizer (exit -> Finding)
    ast.ts                # shared TS/.astro AST helpers (used by PAN002/004/016)
  rules/
    paths.ts              # PAN001
    locales.ts            # PAN002 + PAN003 (locale/kind SSoT parity, via python.ts)
    projects.ts           # PAN004 (corpus collections, bulk kinds, duplicate identity)
    assets.ts             # PAN007 (via python/media_refs.py)
    ownership.ts          # PAN012 (via python/ci_separation.py); PAN005 [planned]
    downloads.ts          # PAN008 (deploy; via python/download_asset_urls.py)
    crawl.ts              # PAN014 (deploy; dist internal-link crawl)
    stack.ts              # PAN016 (source-language + ui-framework)
    imports.ts            # PAN017 (import work-kinds guard) + PAN018 (writer-only
                          #   mutation) — both via python/ checks

    content_quality.ts    # non-blocking heuristics folded from the legacy content audits
    # [planned] urls.ts PAN006, literals.ts PAN006B, css.ts PAN009,
    # [planned] cohesion.ts PAN010, docs.ts PAN011, dead-code.ts PAN013,
    # [planned] retired-surface.ts PAN015
  python/                 # checks the harness subprocesses (PANCRATIUS_AUDIT_ROOT-aware)
    locales.py  kind_segments.py  media_refs.py  work_identity.py
    ci_separation.py  download_asset_urls.py  import_work_kinds.py
    writer_only_mutation.py
  fixtures/<rule-id>/{bad,good}/   # one per gating (core/deploy) rule
```

Tiers map to modes: `core` rules run on `npm run audit` (the PR gate) and
`audit:agent`; `heuristic` rules run only on `audit:agent`; `deploy` rules run
only on `audit:deploy` (they need an emitted `dist/`). Only `fatal` findings exit
non-zero. The harness `fixtures/` tree is excluded from the repo's typecheck/lint
(tsconfig.scripts.json, ruff, ty) because it is crafted — sometimes intentionally
malformed — test data, and a real-repo scan never walks it.

Existing Python audits do not need to be rewritten just to satisfy this shape.
The harness can either call them as subprocesses or let them gradually adopt the
same finding language. Rewriting working audits is justified only when it
reduces duplication or makes a fatal contract more precise.

Rules should be pure scanners when possible:

```txt
rule(context) -> Finding[]
```

They should not mutate files, start dev servers, rewrite Markdown, or regenerate
artifacts. A fixer, if one ever exists, should be a separate command with a
different name and a narrower contract.

Use the right parser for the surface:

- TypeScript compiler API or a focused AST parser for imports/calls where
  false positives would be costly.
- PostCSS for CSS inventories.
- Markdown/frontmatter parsers for content metadata.
- HTML parsing for built `dist/**/*.html` checks.
- Regex only for broad inventories, low-risk smells, or cases where the pattern
  is intentionally textual.

The pretty report matters. A good report shape:

```txt
Pancratius audit
fatal: 0  warning: 3  info: 18

FATAL
  none

WARNING
  PAN011 docs-drift
  docs/content-model.md:31
  Observed: projects are documented as work-bundle entries.
  Contract: projects are themed sections, not downloadable works.
  Repair: update the content-model doc to describe project bundles separately.

INFO
  PAN009 css-duplication
  7 files repeat the project heading ornament block.
  Consider extracting a shared project section heading primitive if the next
  project page needs the same ornament.
```

Do not hide the architecture behind terse linter phrases. A finding should
explain enough that an agent can choose the right abstraction instead of making
the local warning disappear.

## Staging

Do not build every family at once. The first implementation should be the
deterministic fatal core:

- locale route existence and no fallback-rendered localized pages;
- locale/kind/download-format SSOT parity;
- work/project boundary;
- generated/authored ownership and CI import/render/build separation;
- public Markdown/TXT/download leak checks;
- cheap content integrity checks: valid frontmatter, default-locale presence,
  duplicate `(kind, number, lang)`, broken relative assets;
- built-surface crawl, sitemap/feed parity, Pagefind sanity, and download-link
  existence where workflow cost permits.

The second layer is non-blocking agent guidance:

- visible literal inventories;
- CSS duplication and selector reachability;
- component/page cohesion;
- docs drift;
- orphaned/retired code inventories;
- hardcoded derived facts that do not yet produce a broken build artifact.

Promote a warning/info rule to fatal only after it becomes deterministic and is
backed by a real incident or fixture.

## Self-Tests

Rules that can gate (every `core` and `deploy` rule) need their own tests, and
the test is BOTH polarities, not one: a `bad/` fixture the rule MUST fire on, and
a `good/` fixture (a legitimate state / allowed variation) it MUST stay silent
on. The good fixture is the insurance against the rule screaming on a legitimate
future change — it is what decides fatal-vs-warning.

```txt
scripts/audit/fixtures/<rule-id>/
  bad/    # a tiny tree the rule MUST flag (>=1 finding)
  good/   # a tiny legitimate tree the rule MUST NOT flag (0 findings)
```

`npm run audit:selftest` (`selftest.ts`) runs every rule against its fixtures and
ENFORCES the contract: a `core`/`deploy` rule missing either polarity fails the
self-test; `heuristic` (non-gating) rules are exempt. It also shape-checks fired
findings (required fields present, valid severity). Several good fixtures are
deliberate regressions for a real false-positive the build hit — the PAN002
shadowing case, the PAN016 incidental-substring import, the PAN001 `:\]` dialogue
trap. Fixtures are tiny and rule-focused; they exist to stop a rule rotting
silently when the repo changes.

## False Positives And Escapes

Every non-trivial audit needs an escape mechanism, but escapes must be visible.

Acceptable escapes:

- a narrow inline comment with rule id and reason;
- a small allowlist file owned by the audit;
- a documented rule-level exception.

Unacceptable escapes:

- broad ignore directories that hide production source;
- suppressing a fatal rule because it is noisy;
- making the audit pass by adding another renderer rewrite;
- moving content to a new generic bucket to avoid classifying it.

An allowlist entry should expire or explain why it is permanent.

## Relationship To Existing Audits

Existing scripts under `scripts/audit/` are useful but currently behave like a
set of individual checks. The harness should make their contract explicit and
classify them by severity and surface.

Examples:

- locale and kind-segment parity checks are fatal SSOT audits;
- media/download URL checks are fatal deploy-surface audits;
- formatting artifacts and title-language checks are content-quality warnings
  or informational reports unless they prove data loss;
- source coverage is a local library-management audit, not necessarily a site
  deploy blocker.

The goal is not to delete these scripts. The goal is to put them under a shared
language: rule id, severity, category, evidence, repair.

### As implemented

The folds adopted by `python.ts` were given a `PANCRATIUS_AUDIT_ROOT` override
and moved to `scripts/audit/python/`; the unmodified non-blocking ones are folded
read-only in place and run only via `audit:agent`.

| Existing script | Folded as | Tier / severity |
| --- | --- | --- |
| `locales.py`, `kind_segments.py` | PAN003 locale / kind-segment parity | core / **fatal** |
| `media_refs.py` → `python/media_refs.py` | PAN007 asset refs | core / **fatal** |
| `download_asset_urls.py` → `python/download_asset_urls.py` | PAN008 public-Markdown asset URLs | deploy / **fatal** |
| `formatting_artifacts`, `toc_leaks`, `bibliography_leaks`, `rights_boilerplate`, `title_language`, `dialogue_counts`, `docx_semantics`, `size_budget`, `poetry_stanzas`, `verse_blocks`, `source_text_fidelity` | `content_quality.ts` (one heuristic each) | heuristic / warning or info |
| `source_coverage.py` | **not folded** — legacy-dependent local library audit; run manually (`uv run scripts/audit/source_coverage.py`), never CI | local only |
| `run_all.ts` | retired — `harness.ts` is the aggregator | — |

`poetry_stanzas` is a documented promotion candidate: content-model.md says the
stanza audit *should* become a fatal data-loss gate once it is adapted (root
override + a both-polarity fixture). Promotion follows the staging rule — make it
deterministic and back it with a fixture before it gates CI.

## Influences

The useful lesson from harness-engineering practice is to treat the harness as a
first-class product surface: it defines tasks, contracts, evidence, and feedback
loops for future agents. The useful lesson from agent orchestration systems such
as Symphony is to make actions observable and reviewable instead of relying on
intent.

For Pancratius, that means repo-owned contracts and clear findings, not a large
external framework. The harness should be small, static, inspectable, and
specific to this site's architecture.
