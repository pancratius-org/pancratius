# Pancratius Import Pipeline

The contract for **import**: turn an authored source document plus its companion
assets into canonical source Markdown and co-located assets, and write only the
one intended target bundle after explicit safety checks.

Import is one of the three activities in [`architecture.md`](./architecture.md);
it is not release rendering (PDF/EPUB/DOCX/TXT/public Markdown) and not the Astro
build. Those never call import code, and import never renders. The storage shapes
this pipeline must produce are owned by [`content-model.md`](./content-model.md);
the asset, verse, footnote, and bibliography *policies* are owned by
[`content-model.md`](./content-model.md) and [`decisions.md`](./decisions.md).
This document does not restate them — it states how import *honors* them.

## Why two boundaries

Import touches the corpus — committed source of truth — and recovers semantics a
source format expresses only loosely. When parsing, transformation, placement,
and filesystem mutation share one pass, both jobs turn fragile: a parser can copy
media into a work folder as a side effect, and content can be lost or mismarked
as it crosses concerns. The protection is not a framework but **two boundaries**
that cut the work into pure stages with a single mutating tail.

## The two boundaries

Import has exactly two real seams. Everything else is an ordinary function call.

1. **`WritePlan` — the safety boundary.** It separates all import logic from
   filesystem mutation. Import *produces* a plan; it does not write. Only the
   writer applies a plan. This is the boundary that keeps the old fused shape from
   returning: every stage upstream of the writer is pure, so nothing but the
   writer can reach `src/content`.

2. **The block IR — the semantic boundary.** A small typed block model separates
   source-format parsing from Pancratius normalization and lowering. After the
   adapter, nothing is "DOCX-shaped"; it is blocks, footnotes, bibliography, and
   diagnostics.

> Import does not write files. Import produces a `WritePlan`. Only the writer
> applies it.

## Pipeline

```txt
source + companions
  → acquire        (resolve, hash; scratch only — never src/content)
  → parse          (DOCX adapter → block IR; format-specific stops here)
  → normalize      (editorial mechanics over the IR; pure)
  → analyze        (diagnostics over the normalized IR; pure)
  → place          (target from explicit command intent, not from the document)
  → lower          (IR → canonical Markdown + planned assets; pure)
  → plan           (canonical output → WritePlan)
  → write          (the only stage that touches src/content)
```

The stages are small because the boundaries are real, not because "pipeline" is
a nice word. Every stage before `write` is pure or writes only to scratch. Stages
may be fused in code so long as the two boundaries hold; the contract is the
boundaries, not a fixed function count.

## `WritePlan` — the safety boundary

A `WritePlan` is an immutable value: a declared target scope, an ordered set of
write operations expressed as **scope-relative** paths, the diagnostics gathered
upstream, and the ownership/overwrite policy. It never holds absolute target
paths — the writer joins each operation onto the target root and refuses any
result that escapes the scope.

What the boundary buys, in one object: dry-run output for humans and agents;
tests that compare planned writes with no filesystem; one overwrite policy; one
path-boundary policy; and a hard guarantee that no adapter, normalizer, analyzer,
or lowerer can quietly copy media into `src/content`.

Rules the plan must enforce (the writer trusts the plan, so the plan owns these):

- Every *operation* path is relative and stays inside the declared scope;
  absolute paths, `..`, and symlink escapes are refused. (Operation paths are
  outputs. A resolved absolute *source* path is an input to acquisition and
  provenance only — never a write target.)
- A normal import never contains a delete. Pruning is a separate, explicit
  maintenance operation — never part of importing a document.
- Existence is checked per *target file*, not per directory. Adding a new
  language file into an existing bundle is the normal additive case, not a
  collision; overwriting an existing converter-owned `<lang>.md` is refused unless
  replacement is explicitly requested; author-added neighbors are always
  preserved.

## The writer — the only mutator

The writer — a dedicated module under `scripts/lib/` — is the single component
permitted to change `src/content`. It validates the plan's paths, refuses to
apply if any diagnostic is fatal, preflights sources and collisions, then applies
operations through temporary paths and atomic replace. It never pre-deletes
directories. It returns a report of what was created, changed, skipped, and refused.

The writer is **general** — it applies any `WritePlan` to any scope and has no
import-specific opinion. Provenance (below) is the *importer's* policy, written by
the import entry after a successful apply, not by the writer. That is what lets a
non-import mutation — `project page add` scaffolding a sub-page — reuse the writer
unchanged (the same atomic/scoped/no-clobber guarantees and content-general roles)
without emitting an import manifest.

**Ownership.** Files carry provenance: converter-owned (regenerated on
re-import), author-owned (never clobbered without an explicit replace), and
unknown neighbors (always preserved). This is the storage contract from
[`content-model.md`](./content-model.md#work-bundle) — re-import is additive, and
clean-room regeneration is a separate scratch/maintenance path, not the author
workflow.

**Idempotency.** Re-importing the same source yields a byte-identical committed
bundle — same `<lang>.md`, assets, and frontmatter, with no timestamps in
committed output. Volatile provenance (source hashes, tool versions, run time) is
written by the **import entry** (after the writer applies) to a per-work
`data/imports/<work-key>.json` manifest — gitignored, outside the bundle (the
layout in [`content-model.md`](./content-model.md#what-lives-where)) — never in the
committed `<lang>.md` or assets. (This is distinct from `docx_optimize.py`'s
committed `data/conversion-manifest.json`.) Imported body-image filenames are
stable asset IDs after first import, not live checksums (see
[`content-model.md`](./content-model.md#asset-naming)).

**Dry-run** is the review gate: it prints the full planned write-set — including
any replacement it would perform — plus all diagnostics, and touches nothing.
Scope is the target bundle or narrower (a single added language file is a valid
narrower scope); replacement is required only to overwrite an existing
converter-owned file, never to add a new one.

## The block IR — the semantic boundary

A typed block model, not a compiler AST. It carries only the block and inline
kinds Pancratius canonical Markdown actually needs — prose, verse with stanza
structure, role-tagged blockquotes and tables, asset-id images, thematic breaks,
emphasis, links, code, footnote references — plus an explicit *unknown* block and
*unknown* inline for anything unrecognized. The authoritative kind set lives in
code, not here; the contract is the shape, not the inventory. Footnote
definitions, the lifted bibliography, and diagnostics travel beside the blocks,
not inside the prose.

Frontmatter is seeded by the importer, not carried inside the IR. The importer
starts from the existing bundle's `<lang>.md` frontmatter (so author-owned fields
survive a re-import), then layers explicit CLI overrides and values inferred from
the source document (a title read from the document core or filename, a `TODO`
description seed, the lifted `cross_refs`/`bibliography`). The blocks carry only
reading content; seeding frontmatter is a separate concern in the importer, so it
never reaches back into the blocks.

The model is deliberately small: source-specific style noise becomes a
diagnostic, not a block type; a raw Markdown string is too weak to preserve
stanza, footnote, image-role, and source-span information, and a full source AST
is too broad to be the Pancratius model. If a future need appears, add a block
type — do not smuggle structure through string conventions.

### One adapter now: DOCX

The parser turns one source format into the IR. **Only the DOCX adapter exists.**
The other formats named in earlier drafts (Markdown, HTML, text, ODT) are *not*
built; the IR is the seam that would let them be added later without touching
placement, lowering, or the writer. Designing that seam costs nothing; populating
it now would be speculative surface, so we do not.

*How* the DOCX adapter reads the document — which structured parse feeds the IR
and which narrow OOXML signals are read directly for things a text writer drops
(empty paragraphs, alignment, named styles, footnote linkage, image
relationships) — is an implementation detail behind the adapter, fixed by
measured fidelity in code and its tests, not pinned in this contract. The
invariant is only this: **no Markdown string exists before lowering.** The
adapter does not parse to GFM and then patch the string.

## The transformation layer must be editable in one place

Detection, normalization, and lowering rules are the part that actually changes
over time (how verse is detected, how a footnote lowers, how an epigraph is
recognized). Each such rule is a local edit to a normalization or lowering pass —
it must not ripple through parse, placement, or write. If changing "how verse is
detected" forces edits in the adapter or the writer, the boundary has leaked.

The *substance* of these rules is the body contract in
[`content-model.md`](./content-model.md#markdown-body-contract) and the styling
decisions in [`decisions.md`](./decisions.md) (verse/stanza handling, Q/A answer
runs, right-aligned signatures and epigraphs, thematic breaks, divine-voice
non-marking, bibliography lift). Import implements those; it does not invent its
own. Empty source paragraphs are meaningful and must be captured in the IR before
any Markdown output could lose them.

## Footnotes are first-class

Footnotes are source content and stay structured — definitions and references,
linked — all the way to lowering. They are not a string artifact of the parser.

**An unresolved footnote reference is fatal.** A reference with no definition
blocks the write. This is the contract that kills the shipped failure where
endmatter stripping dropped definitions and left orphaned `[^N]` markers in the
body. A definition with no reference, a duplicated id, or a footnote whose body
points at another work are non-fatal diagnostics (warning/info).

## Placement comes from the command, not the document

The source format never decides product ontology. A DOCX can become a book, a
poem, a project subpage draft, or a plain draft depending only on the explicit
command. Placement maps an explicit intent to a target scope and a frontmatter
seed; it does not infer the kind from the file.

`import` writes **works** (book/poem) only — projects are themed sections, not
converter output (see [`content-model.md`](./content-model.md#projects)). Project
subpages are *scaffolded* into their own subpage directory and never edit the
project landing; promotion of project material to a real work is an editorial
decision, never a tool flag.

## Diagnostics

Diagnostics are first-class values with a severity, not stderr text. **Fatal**
blocks the write (scope escape, refused overwrite, unresolvable local image,
unresolved footnote reference, parse failure, a subpage scaffold that would touch
the landing). **Warning** does not block but must print before the write summary
(guessed title, `TODO` description seed, capped image, ignored unknown style,
table classified as bibliography, dropped source frontmatter keys). **Info**
records provenance and candidates. The rule from
[`architecture.md`](./architecture.md): when the tool is guessing, the user sees
a diagnostic.

## Import is the content-safety boundary

The published Markdown is rendered without a sanitizer — verse-blocks,
signatures, and bidi spans carry converter-emitted raw HTML the pages depend on.
The importer is therefore the boundary that makes authored content safe before it
reaches the corpus: literal `Text` is escaped (Markdown/HTML metacharacters,
variable-length code fences) so it cannot become active markup; link and image
URL schemes are allowlisted (http/https/mailto and relative/anchor targets;
others dropped with a diagnostic); an unresolvable or scope-escaping local image
is a fatal write-refusal; and imported body-image SVGs are sanitized at the
writer's copy boundary. See
[`decisions.md`](./decisions.md#import-is-the-publish-gate-harden-authored-content-not-the-renderer).

## What import must never automate

Editorial judgment is never a tool output: whether a document is a book or a
project subpage, the final title or description, theological framing, project
landing composition or subpage order, featured-book curation, promotion to a
work, and translation approval. When the tool cannot know, it emits a diagnostic
and stops short of deciding.

## Command surface

The verbs live in [`tooling.md`](./tooling.md); this pipeline backs the import
ones. `work import` writes work bundles; `project page add` scaffolds a subpage
only. Both support `--dry-run`. (`docx optimize` is not import — it is
source-artifact maintenance with its own write policy; see
[`tooling.md`](./tooling.md).)

The CLI is a thin facade over **library entries**, not other CLIs. `work import`
dispatches to `import_work(ImportRequest) -> WriteReport`; `project page add`
dispatches to a sibling `scaffold_subpage(...) -> WriteReport` co-located with the
conversion lib. Both run the plan→writer tail and return the writer's report (the
planned/applied write-set plus diagnostics). So adding the CLI is wiring, not a
rewrite.

## How import is verified

Two distinct surfaces — see [`audit-harness.md`](./audit-harness.md) and
[`tooling.md`](./tooling.md) for the distinction. A **test** asserts behavior by
running code on inputs; an **audit** asserts a static invariant by reading the
tree.

**Tests** (pytest / `node:test`): golden fixtures from real corpus works (updated
only on deliberate behavior changes, with the diff reviewed); idempotency
(import twice → byte-identical); invariants (every footnote resolves, no
cover-as-body, no machine-local paths, verse blocks non-empty); `WritePlan`
rejection cases (absolute, `..`, symlink, existing-without-replace, out-of-scope,
delete-in-normal-import) with no filesystem; writer integration on a temp tree
(atomic replace, manifest written, author neighbors untouched, dry-run writes
nothing, subpage scaffold touches only the subpage dir, the CLI refuses
`project` at runtime).

**Audits** (PAN rules in the harness — derive-from-SoT, deterministic-fatal,
both-polarity fixtures; see [`audit-harness.md`](./audit-harness.md)): the import
CLI's kind choices exclude `project`, derived from the work kinds (PAN017); a
source scan asserting that filesystem mutation into `src/content` happens only in
the writer module — every pure import module carries a marker and the scan derives
its set from those markers, so the boundary holds regardless of test coverage
(PAN018); and import/converter code is never invoked from CI, neither the
importer/renderer scripts nor the converter/IR/writer library modules behind them
(PAN012). These guard the *shape* so the boundary cannot silently drift; the
runtime behaviors above stay in tests, where a property is established by running
the code, not by guessing from its shape.

## Final rules

1. Source adapters parse into the IR; they do not place or write.
2. Normalizers and analyzers transform and diagnose; they do not write.
3. Placement comes from explicit command intent, never from the source format.
4. Lowering produces canonical source content, not public exports.
5. Import produces a `WritePlan`; only the writer applies it.
6. A normal import writes one target scope and never deletes.
7. Footnotes resolve or the import fails.
8. Re-import is byte-identical; volatile provenance lives outside the bundle.
9. Projects are scaffolded as authored sections, not imported as works.
10. When the tool is guessing, the user sees a diagnostic.

## Non-goals

No generic page-builder, no plugin framework, no speculative format adapters, no
CI import/render, no treating public Markdown/TXT/PDF/EPUB as source truth, and
no letting the source format decide product ontology.
