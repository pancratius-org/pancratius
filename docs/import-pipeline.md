# Pancratius Import

Import turns an authored source document plus companion assets into canonical
source Markdown and co-located assets. It writes one explicit target bundle after
safety checks.

Import is library work. It is not release rendering and not the Astro build.
Those never call import code, and import never renders. The storage shape is
owned by [`content-model.md`](./content-model.md); asset, verse, footnote, and
bibliography policy live there and in [`decisions.md`](./decisions.md).

## Boundaries

Import touches committed source and recovers semantics from loose source
formats. The parser, transformation logic, placement logic, and filesystem
mutation stay separated so a source-format adapter cannot quietly write into
`src/content` or lose content while deciding placement.

1. **`WritePlan` — filesystem boundary.** Import produces a plan; it does not
   write. Only the writer applies a plan. Every stage upstream of the writer is
   pure or scratch-only, so adapters and passes cannot reach `src/content`.

2. **Block IR — semantic boundary.** A small typed block model separates
   source-format parsing from the Pancratius passes and lowering. After the
   adapter, nothing is DOCX-shaped; it is blocks, footnotes, and bibliography.
   Diagnostics flow through one sink on the pass context to the composition
   point, never on the document.

> Import does not write files. Import produces a `WritePlan`. Only the writer
> applies it.

## Pipeline

Import is compiler-shaped: parse a source format into a small domain IR, run
passes over that IR, lower it to Pancratius Markdown/assets, then hand a write
plan to the only filesystem mutator. This keeps DOCX quirks out of site storage
and gives tests a stable semantic surface.

```txt
source + companions
  -> acquire        resolve sources; scratch only, never src/content
  -> parse          DOCX/OOXML adapter -> Block IR; format-specific stops here
  -> passes         the declared pass pipeline over the IR; pure, deterministic
                    given the injected parameters
  -> place          target from explicit command intent, not from the document
  -> lower          IR -> canonical Markdown + planned assets; pure
  -> plan           canonical output -> WritePlan
  -> write          the only stage that touches src/content
```

Every stage before `write` is pure or writes only to scratch. Stages may be
fused in code so long as the two boundaries hold; the contract is the boundaries,
not a fixed function count.

Passes are code; models and policies are **parameters**. Every pass is a plain
function over the document; anything tunable — a trained register model, the
slug lookup, demotion depth — is injected through the pass context at the
composition point. Rules-only register import is an explicit policy, not the
implicit fallback for a broken production artifact; the default model-assisted
register rollout requires its committed bundle for covered languages and uses a
diagnosed rules fallback only for languages outside that rollout. IR nodes carry
source facts and provenance only; feature vectors are derived at decision time,
never stored on the IR. Research and training live outside the package; the
package ships the passes and reads committed model artifacts.

The pipeline is declared data: an ordered tuple of named passes. Observation
happens at named pipeline positions — an observer runs the same pipeline
`until=` a named pass — never via flags or a parallel orchestrator.

## `WritePlan`

A `WritePlan` is an immutable value: a declared target scope, an ordered set of
write operations expressed as **scope-relative** paths, the diagnostics gathered
upstream, and the ownership/overwrite policy. It never holds absolute target
paths — the writer joins each operation onto the target root and refuses any
result that escapes the scope.

A single plan gives dry-run output, write-set tests without filesystem mutation,
one overwrite policy, one path-boundary policy, and a hard rule that only the
writer can copy media into `src/content`.

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

## Writer

`pancratius/writer.py` is the only component allowed to change `src/content`. It
validates plan paths, refuses fatal diagnostics, preflights sources and
collisions, then applies operations through temporary paths and atomic replace.
It never pre-deletes directories. It returns what was created, changed, skipped,
and refused.

The writer applies any `WritePlan` to any scope. It has no import-specific
policy. Provenance is written by the import entry after a successful apply, not
by the writer. That lets `project page add` reuse the same scoped/no-clobber
write path without emitting an import manifest.

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
committed `<lang>.md` or assets. Imported body-image filenames are stable asset
IDs after first import, not live checksums (see
[`content-model.md`](./content-model.md#asset-naming)).

**Dry-run** is the review gate: it prints the full planned write-set — including
any replacement it would perform — plus all diagnostics, and touches nothing.
Scope is the target bundle or narrower (a single added language file is a valid
narrower scope); replacement is required only to overwrite an existing
converter-owned file, never to add a new one.

## Block IR

A typed block model, not a compiler AST. It carries only the block and inline
kinds Pancratius canonical Markdown actually needs — prose, lineated runs with
stanza structure, quotes, lists, tables, asset-id images, thematic breaks,
emphasis, links, inline code, footnote references — plus an explicit *unknown* block and
*unknown* inline for anything unrecognized. The authoritative kind set lives in
code, not here; the contract is the shape, not the inventory. Footnote
definitions and the lifted bibliography travel beside the blocks, not inside the
prose; diagnostics flow through the pass-context sink.

Block semantics are two axes, encoded once. The **substrate** set (what a block
*is*: paragraph, heading, lineated run, quote, …) is closed — extending it is a
deliberate IR change. The **`Register`** field on the set-apart substrates (what
voice a block speaks in: ordinary, verse, scripture, inset, …) is the open axis.
Adding a register is a fixed-size change: one enum member, one row in each total
register→emission mapping table (pinned by totality tests), one CSS contract for
the emitted class. A register without a product CSS contract lowers to the
conservative base emission — under-styling is the cheap failure.

Frontmatter is seeded by the importer, not carried inside the IR. The importer
starts from the existing bundle's `<lang>.md` frontmatter (so author-owned fields
survive a re-import), then layers explicit CLI overrides and values inferred from
the source document (a title read from the document core or filename, a `TODO`
description seed, the lifted `cross_refs`/`bibliography`). The blocks carry only
reading content; seeding frontmatter is a separate concern in the importer, so it
never reaches back into the blocks.

The model is deliberately small. Source-specific style noise becomes a
diagnostic, not a block type. A raw Markdown string cannot preserve stanza,
footnote, image-role, and source-span information; a full source AST is too broad
for the Pancratius model. Add a block type when structure is real. Do not smuggle
structure through string conventions.

`source_span` is one validated provenance value. It carries ordered natural-line
coordinates where the adapter can prove them; its enclosing paragraph interval
is derived from those coordinates and remains the stable raw DOCX slice for
diagnostics. Its display-line groups are also ordered and total over those exact
coordinates. A transform that intentionally removes a display boundary groups
the adjacent coordinates on the surviving line rather than restoring content or
guessing by text. Passes partition exact coordinates when they split
source-derived blocks and combine them when they merge blocks. Lowering ignores provenance.
Diagnostics such as `pancratius docx inspect` use it to map IR decisions back to
the DOCX slice, intersecting per-paragraph classifications with the source
aggregate's semantic ordinals so skipped pagination/empty rows cannot be swept
back in by the interval. Span-only provenance remains valid for synthetic or
non-DOCX IR, but line-level observers cannot invent coordinates from it.

### One adapter now: DOCX

The parser turns one source format into the IR. **Only the DOCX adapter exists.**
The other formats named in earlier drafts (Markdown, HTML, text, ODT) are not
built. The IR boundary would let them be added later without touching placement,
lowering, or the writer. Adding them now would be speculative surface.

The DOCX adapter consumes exactly one `DocxSourceDocument`, built by
`docx_source.read`. That immutable aggregate is the authoritative interpretation
of the package for import. It is deliberately domain-sufficient rather than a
general OOXML object model. One package read produces:

- ordered body blocks and rich inlines, including runs, links, images, fields,
  notes, lists, tables, text boxes, and content controls;
- paragraph identity, typed authored/layout breaks, structural emptiness,
  resolved and direct styles, numbering, alignment, borders, and the narrow
  geometry needed by later analysis;
- relationship-resolved media and note definitions; and
- explicit diagnostics or unknown nodes for readable constructs outside the
  supported grammar.

The rich and physical views are not joined by text, position guesses, or injected
anchors. A body paragraph block carries its `SourceAddress` and the exact
`SourceParagraph` value from the same parse. Body paragraph ordinals remain the
stable editorial coordinate for correction sidecars; structural addresses cover
table cells, notes, content controls, and text boxes. Location establishes
identity and diagnostic attachment, not lineation or register.

Paragraph content is one ordered, closed sequence of text fragments and typed
breaks (`line`, `page`, `column`). Reading text, natural lines, and break evidence
are derived views, never separately parsed copies. Paragraph disposition
explicitly distinguishes readable content, structural emptiness, pagination-only
layout, and opaque non-text content. Markup-compatibility content selects one
supported branch at every nesting level; inactive choices are never concatenated
or used as evidence.

Field parsing also owns field-result membership across paragraph boundaries,
including empty result rows. The adapter carries generated-TOC membership as a
typed source fact, and the TOC pass filters that fact; it does not rediscover a
TOC from link targets, indentation, or text shape.

Internal nonbreaking spaces are authored text and survive rich-run normalization,
including at emphasis boundaries. Whitespace at the outer edge of a paragraph is
layout and is discarded before lowering.

The adapter is a package-blind typed projection from this aggregate into block
IR. It does not open the package, invoke Pandoc, walk XML, reconcile a second
tree, or manufacture source identity. Its only I/O is materializing media bytes
already carried by the aggregate into the import scratch directory. Diagnostics,
correction rails, audits, and research producers project from the same aggregate.
A consumer that needs an import-semantic DOCX fact must extend the aggregate
instead of rereading the package. Package validation, optimization, slicing,
merge, independent rendering, and translation writing are different operations
and may use lower-level package access without becoming alternate semantic
readers.

**No Markdown string exists before lowering.** Empty source paragraphs and other
source evidence enter the IR before any textual representation could erase them.

## The transformation layer must be editable in one place

Detection, normalization, and lowering rules are the part that actually changes
over time (how verse is detected, how a footnote lowers, how an epigraph is
recognized). Each such rule is a local edit to one pass or one lowering table —
it must not ripple through parse, placement, or write. If changing "how verse is
detected" forces edits in the adapter or the writer, the boundary has leaked.
Structurally: a register *rule* change is an edit in `passes/register.py`; a
register *mapping* change is a table row in `lower.py`.

The *substance* of these rules is the body contract in
[`content-model.md`](./content-model.md#markdown-body-contract) and the styling
decisions in [`decisions.md`](./decisions.md) (verse/stanza handling,
right-aligned signatures and epigraphs, thematic breaks, divine-voice
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

The published Markdown is rendered without a sanitizer — lineated wrappers,
signatures, and bidi spans carry converter-emitted raw HTML the pages depend on.
The importer is therefore the boundary that makes authored content safe before it
reaches the corpus: literal `Text` is escaped (Markdown/HTML metacharacters,
variable-length code fences) so it cannot become active markup; link and image
URL schemes are allowlisted (http/https/mailto and relative/anchor targets;
others dropped with a diagnostic); an unresolvable or scope-escaping local image
is a fatal write-refusal; and imported body-image SVGs are sanitized at the
writer's copy boundary. See
[`decisions.md`](./decisions.md#import-is-the-publish-gate-harden-authored-content-not-the-renderer).

## Limits

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
dispatches to `scaffold_subpage(...) -> WriteReport`. Both run the plan→writer
tail and return the writer's report: the planned/applied write-set plus
diagnostics.

## How import is verified

Two distinct surfaces — see [`audit-harness.md`](./audit-harness.md) and
[`tooling.md`](./tooling.md) for the distinction. A **test** asserts behavior by
running code on inputs; an **audit** asserts a static invariant by reading the
tree.

**Tests** (pytest / `node:test`): golden fixtures from real corpus works (updated
only on deliberate behavior changes, with the diff reviewed); idempotency
(import twice → byte-identical); invariants (every footnote resolves, no
cover-as-body, no machine-local paths, lineated wrappers non-empty); `WritePlan`
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
importer/renderer tools nor the converter/IR/writer library modules behind them
(PAN012). These guard the *shape* so the boundary cannot silently drift; the
runtime behaviors above stay in tests, where a property is established by running
the code, not by guessing from its shape.

Checks that consume `DocxSourceDocument` begin at the canonical-source boundary.
They can catch transformation and committed-output loss, but they are not an
independent OOXML interpretation and must not be described as reader validation.
Reader coverage comes from the element-identity coverage assertion, explicit
unknown/unsupported diagnostics, focused source-grammar fixtures, and a reusable
[cross-version differential harness](../tests/tools/docx_frontend_parity.py).
Adding a second text or stanza extractor to an audit would recreate the drift
this boundary removes.

## Final rules

1. Source adapters parse into the IR; they do not place or write.
2. Passes transform and diagnose; they do not write.
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
