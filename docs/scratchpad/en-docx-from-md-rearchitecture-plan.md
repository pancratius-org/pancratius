# English DOCX Transfer Re-Architecture Plan

This is a working plan for graduating the translated-DOCX prototype into a
public-quality Pancratius command.

It is not yet the architecture contract. Once the shape is implemented and
reviewed, the stable parts should move into `docs/tooling.md` and a small
formal architecture note. Until then this file is the handoff for agents and
reviewers.

## Goal

Bootstrap faithful English DOCX source artifacts from:

- the committed Russian donor DOCX,
- the committed Russian Markdown imported from that DOCX,
- the committed English Markdown translated from the Russian Markdown.

The output DOCX files are committed corpus artifacts. The site build must not
generate them. After a translated DOCX exists, it is source in the normal
Pancratius model; the English Markdown should be derived from that DOCX through
the importer, not maintained as the long-term authority.

The quality target is not "Pandoc made a readable DOCX." The target is a
donor-shaped English edition that preserves the source DOCX structure where
that structure is meaningful: paragraph shape, drawings, media relationships,
footnote mechanics, cover fields, hyperlinks, and Word layout intent.

## Current State

The prototype is real and verified on top of current `main`.

Current implementation:

- command: `pancratius docx translate-from-md [book:NN] [--lang en] [--dry-run] [--replace]`
- package owner: `pancratius.translation.docx`
- implementation file: `pancratius/translation/docx/pipeline.py`
- facade: `pancratius/translation/docx/__init__.py`
- tests: `tests/python/test_docx_translate.py` and CLI coverage in `tests/python/test_cli.py`
- generated artifacts: 45 new `src/content/books/*/en.docx` files

The command shape follows current CLI architecture:

- resource identity is a typed positional selector such as `book:9`;
- `--book` is retired by PAN024 and must not come back;
- the command remains under the `docx` CLI group because the user action is DOCX
  maintenance;
- the implementation lives under `translation/docx` because the capability is
  translated artifact transfer.

The verified gate after the main merge:

- focused DOCX and CLI tests pass;
- `npm run verify` passes with `UV_CACHE_DIR=/private/tmp/pancratius-uv-cache`;
- repo audit and post-build audit are clean;
- Python suite passes with the existing known xfail.

## Core Judgment

This pipeline is not a new backend for the import IR and not an ongoing
Markdown-to-DOCX renderer.

It is a three-way document transfer:

- donor: `ru.docx`, whose structure is preserved;
- alignment key: `ru.md`, which explains how imported text maps to the donor;
- bootstrap content: `en.md`, which supplies translated text and inline meaning
  before the English DOCX exists;
- result: `en.docx`, written into the same committed source-artifact slot as the
  Russian DOCX.

The import IR is the wrong spine for this operation. The import pipeline's job
is to normalize DOCX into canonical Markdown. After the adapter, it no longer
keeps live Word runs, relationships, drawings, or package parts. Transfer needs
those live donor structures by design.

The right connection to the import pipeline is shared vocabulary, shared
matching primitives, and a hard round-trip check: generated English DOCX must
import back to acceptable English Markdown.

## Source Authority

The lifecycle is:

1. English Markdown exists as an internal translation artifact.
2. `docx translate-from-md` transplants that text into the Russian donor DOCX
   structure and creates `en.docx`.
3. From that point, `en.docx` is source. If an author edits the English book,
   they edit DOCX.
4. The normal importer derives `en.md` from `en.docx`.

Do not add an embedded provenance or freshness protocol to the DOCX package.
That would fight the repo's normal source model and make author edits look like
pipeline drift. Protect existing English DOCX files through command semantics:
batch mode creates missing files only, and `--replace` requires an explicit
`book:NN`.

The important proof is therefore not "does `en.md` still match `en.docx` by
hash?" It is "does imported Markdown from `en.docx` preserve the text and
structure we need?"

## Non-Goals

Do not turn this into:

- a generic Markdown-to-DOCX renderer;
- a reverse import-IR backend;
- a `render_downloads` step;
- a CI-generated artifact path;
- an importer reorganization;
- an editor for translation prose.

Before a translated DOCX exists, if English Markdown is wrong, fix the Markdown
or route through the text translation machinery. After the DOCX exists, fix the
DOCX and re-import Markdown.

If a Russian DOCX has a genuine source bug, fixing it is allowed, but that is a
source repair, not something hidden inside transfer logic.

## Why Not Render From Markdown

The cheap alternative is template-rendering `en.md` into `en.docx`.

That path is only correct if we do not care about preserving the author's
hand-shaped source DOCX design. The current requirement does care. Many of these
documents carry structure that Markdown cannot express without a large reference
document and side channels: drawings, odd cover fields, footnote reference
shape, Word paragraph choices, relationships, and source-only layout material.

So the transfer pipeline is justified for this corpus.

The docs should state this directly. Otherwise future agents will keep asking
why this is not just Pandoc.

## Desired Package Shape

Target package:

```text
pancratius/translation/docx/
  __init__.py
  models.py
  markdown_units.py
  donor_docx.py
  align.py
  transfer.py
  ooxml_write.py
  batch.py
  report.py
  audit.py
```

The split should be behavior-preserving first. Do not refactor while moving.

### `models.py`

Owns domain types that describe the transfer problem:

- `BookDocxTranslationTarget`
- `DocxTranslationReport`
- `DocxTranslationBatch`
- `MarkdownTransferDocument`
- `MarkdownTransferUnit`
- `TranslatedTextRun`
- `FootnoteAnchor`
- `WordTextSlot`
- `SourceDocxAlignmentPlan`
- `TransferAlignment`
- `MarkdownUnitPairing`
- narrow enums or literals for unit kind, ignored slot reason, and alignment
  variant reason

The types should carry corpus language, not generic document-processing names.
This code is easier to maintain when grep sees the domain.

### `markdown_units.py`

Owns Markdown ingestion.

Responsibilities:

- call Pandoc only as a Markdown AST reader;
- define the supported Markdown flavor in one place;
- project source and translated Markdown into transfer units;
- preserve enough inline structure for DOCX writing: plain text, emphasis,
  strong, links, images, footnote anchors, and raw wrappers that the corpus
  actually uses;
- emit diagnostics for unsupported structures.

It should not know about OOXML.

The Markdown flavor should be reviewed against `translation/text/document.py`.
That module is not a drop-in representation, but its round-trip identity
discipline is the right bar.

### `donor_docx.py`

Owns donor package reading.

Responsibilities:

- open DOCX as a ZIP package;
- parse relevant OOXML parts;
- expose `WordTextSlot` records with live paragraph elements or safe handles;
- track drawings, footnote references, hyperlink relationships, and media parts;
- identify source-only paragraphs that can be ignored only through named rules.

It should not know about English Markdown.

### `align.py`

Owns the domain crux: the alignment plan.

Responsibilities:

- align `ru.md` units to donor DOCX slots;
- pair `ru.md` units to `en.md` units;
- produce a complete `SourceDocxAlignmentPlan`;
- fail closed when alignment is not proven;
- expose diagnostics that explain every exception.

This should become the most tested module in the package.

The current named alignment variants are valuable because they make corpus
quirks reviewable. Keep that shape. Avoid an opaque "fuzzy match" bucket.

### `ooxml_write.py`

Owns DOCX package mutation.

Responsibilities:

- replace donor run text with translated inline runs;
- preserve or rebuild footnote references with one clear ordering convention;
- preserve hyperlinks and relationships;
- preserve valid drawings;
- replace cover data URI fields from translated frontmatter;
- scrub Cyrillic drawing metadata;
- validate the written OOXML package.

It may write to a temporary output path. It must not write into `src/content`
directly. Corpus mutation stays behind `WritePlan` and `writer.apply`.

### `transfer.py`

Owns one-book orchestration.

Responsibilities:

- parse source and translated Markdown;
- read the donor DOCX package;
- build the alignment plan;
- call the OOXML writer;
- validate the staged DOCX;
- return unit counts and diagnostics.

It should not scan the corpus and should not apply a `WritePlan`.

### `batch.py`

Owns corpus target discovery and `WritePlan` construction.

Responsibilities:

- scan `src/content`;
- select missing translated DOCX targets, or one explicit `book:NN`;
- refuse corpus-wide replacement of existing translated DOCX files;
- call the lower-level transfer path;
- build `WritePlan` operations;
- call the writer;
- return a typed batch report.

It should know about corpus layout and writer policy. It should not know about
OOXML internals.

### `report.py`

Owns human and optional JSON output.

The CLI should stay thin:

- parse flags and selectors;
- call the owner function;
- print the owner report;
- map refusal/failure to exit codes.

Add `--json` only when the report shape is stable enough to support automation.

### `audit.py`

Owns artifact-level checks that can become a repo audit rule.

This is separate from transfer execution. CI must verify committed DOCX files,
not generate them.

## Shared Matching Kernel

There is useful prior art in `docx_adapter.reconcile_source`.

Do not import transfer code from the importer, and do not put transfer types
inside `pancratius/ir`. Instead, extract a small neutral kernel only after the
module split makes the common shape obvious.

Candidate module:

```text
pancratius/source_match.py
```

Candidate primitives:

- word normalization;
- text fingerprints;
- monotone anchor matching;
- windowed record matching;
- match diagnostics or confidence reasons.

Both the importer and DOCX transfer can depend on that neutral kernel.

Do not extract importer-private types. The shared layer should operate on small
plain records or protocols such as:

```text
record id
plain text
optional source hint
```

The transfer package should keep its own domain types. The importer should keep
its own IR types.

## Footnote Decision

Pick one footnote ordering convention and encode it as a domain rule.

The likely rule:

- translated Markdown footnote anchors are ordered by appearance in translated
  units;
- donor footnote reference runs are reused where the aligned donor slot has the
  matching anchor count;
- footnote definitions are rebuilt densely for the output package;
- mismatched anchor counts fail closed unless a named, tested exception exists.

This should be consistent with the import side's dense `FootnoteRef` behavior.
Do not let `FootnoteAnchor(ordinal)` become a second, undocumented convention.

## Determinism

Generated DOCX files are committed bytes during bootstrap. Re-running transfer
for the same explicit book should not create random diffs.

This is an implementation requirement, not just a test wish. The writer must set:

- ZIP member ordering;
- ZIP timestamps with an explicit fixed timestamp;
- compression type and level;
- stable XML namespace serialization;
- stable relationship ids where possible.

Add tests or an audit for:

- byte-identical rerun for a small fixture;
- no new `xmlns:nsN` ElementTree prefixes in the small transfer fixture;
- every prefix named by `mc:Ignorable` or related compatibility attributes is
  declared in the same XML part;
- at least package-equivalent rerun for corpus files if byte identity is blocked
  by an external library detail.

If byte identity is not possible, document the exact reason and compare a
canonicalized package form instead. Do not hand-wave this.

## Artifact Audit

The prototype already proved useful checks outside the code. Graduate them into
the repo.

Likely rule name:

```text
PAN025-translated-docx-transfer
```

Checks:

- every committed translated DOCX is a valid OOXML package;
- every relationship target resolves;
- media relationships match package media parts;
- no Cyrillic drawing metadata remains in English DOCX files;
- footnote references and footnote definitions agree;
- cover field data URI matches translated frontmatter when present;
- known source-only drawings are preserved;
- obvious Russian body leakage is reported, with a narrow allowlist for
  citations or source names if needed;
- command-owned generated artifacts are not produced in CI.

The audit should inspect committed bytes. It should not call the converter.

Do not make undeclared `mc:Ignorable` prefix values a repo-wide fatal audit until
the existing Russian source DOCX corpus is normalized; several source files
already carry Word-tolerated compatibility prefix values without matching
declarations. Keep that check on transfer-output fixtures first.

## Round-Trip Gate Finding

The DOCX-first lifecycle is still the right direction, but it is not safe to
flip source authority by policy alone.

Current regenerated sample:

- `08`, `23`, `46`, `53`, and `75` all import successfully from `en.docx` back
  to Markdown in a temp content root.
- None are byte-identical to the bootstrap `en.md`.
- The main diffs are frontmatter loss (`translation.model` and
  `translation.generated_at`) and Markdown emphasis-span drift caused by Word run
  structure.

A separate adversarial import experiment on pre-existing English DOCX files found
larger failures in some books: mixed-script English text, stale DOCX versus
Markdown, semantic line drift, and frontmatter mutation.

Therefore the source-authority rule must be:

- transfer creates the initial `en.docx`;
- `en.docx` becomes the intended source only after a round-trip gate passes for
  that book;
- the gate should compare semantic text and structure, not raw Markdown bytes;
- importer work is needed before this can be made automatic, at least to preserve
  existing frontmatter fields and detect mixed-script English regressions.

## CLI Contract

Keep the public command:

```sh
uv run pancratius docx translate-from-md [book:NN] [--lang en] [--dry-run] [--replace]
```

Rules:

- zero selectors means all missing translated DOCX artifacts;
- one `book:NN` selector means one book;
- source locale is refused by argparse choices;
- existing translated DOCX is treated as source and skipped in batch mode;
- `--replace` requires an explicit `book:NN` selector because it discards
  possible DOCX-side edits;
- `--limit` is for batch discovery only and should be rejected with an explicit
  selector if that ambiguity becomes real;
- no `--book`, `--number`, or other partial identity flags.

Later, if multiple explicit selectors are needed, add a typed request object and
tests first. Do not grow ad hoc handler logic.

## Documentation

After the module split and artifact audit land, add a short formal doc.

Possible file:

```text
docs/translated-docx-transfer.md
```

It should cover:

- why transplant exists instead of Markdown rendering;
- the three inputs and one output;
- how alignment fails closed;
- what the command changes;
- what CI verifies;
- what it deliberately does not do.

Keep implementation status and migration notes out of the formal doc.

## Graduation Sequence

### 1. Freeze Current Behavior

Before splitting files:

- keep the current tests green;
- keep the generated corpus artifacts unchanged;
- record the current command help;
- record current failure cases from the corpus, if any.

This prevents a file move from becoming a behavior rewrite.

### 2. Split The Package

Move code out of `pipeline.py` into the target modules.

Order:

1. `models.py`
2. `markdown_units.py`
3. `donor_docx.py`
4. `align.py`
5. `ooxml_write.py`
6. `batch.py`
7. `report.py`

Run focused tests after each large move. The first pass should change imports,
not behavior.

### 3. Harden Alignment

Make `align.py` the review center.

Add tests for:

- one-to-one paragraph alignment;
- source DOCX paragraph splits;
- Markdown lineated blocks;
- footnote-bearing paragraphs;
- source-only DOCX paragraphs;
- every named alignment variant;
- refusal when a variant would be unsafe.

Then extract `source_match.py` only if it makes both importer and transfer code
clearer.

### 4. Harden Package Writing

Add package-level tests for:

- footnotes;
- hyperlinks;
- images;
- cover fields;
- source-only drawings;
- relationship cleanup;
- Cyrillic metadata scrub;
- deterministic package output.

The writer module should have no corpus discovery and no direct `src/content`
mutation.

### 5. Add The Artifact Audit

Move the external artifact checks into `audit/`.

Add self-test fixtures for the new rule. The bad fixture should fail for one
clear reason. The good fixture should be small and stable.

Run:

```sh
UV_CACHE_DIR=/private/tmp/pancratius-uv-cache npm run audit:repo
UV_CACHE_DIR=/private/tmp/pancratius-uv-cache npm run verify
```

### 6. Publish The Contract

Promote the stable explanation into formal docs.

Update:

- `docs/tooling.md`
- a new short translated-DOCX transfer doc, if needed
- CLI help and tests

Do not document internals that are still likely to move.

### 7. Final Corpus Pass

Bootstrap or verify the committed English DOCX files.

Checks:

- `pancratius docx translate-from-md --dry-run` reports nothing missing;
- representative generated English DOCX files import into temporary Markdown
  without text loss or structural damage;
- the imported Markdown diff against the bootstrap English Markdown is explained
  and small enough to accept, or the importer/transfer path is fixed;
- generated artifacts pass the new audit;
- full verify passes;
- a small manual sample opens in Word/LibreOffice if available.

## Review Questions

Reviewers should challenge these points:

- Is donor transplant still the right product choice for English DOCX?
- Are any transfer types accidentally import-IR types in disguise?
- Does `translation/docx` depend on importer internals?
- Does any module write into `src/content` outside `WritePlan`?
- Are alignment exceptions named and tested?
- Can a re-run produce unexplained DOCX diffs?
- Does generated `en.docx` import back to acceptable `en.md`?
- Does CI verify committed artifacts without generating them?
- Is the CLI grammar selector-first and audit-compliant?

## Final Shape

The public artifact should feel simple:

```sh
uv run pancratius docx translate-from-md
uv run pancratius docx translate-from-md book:32 --replace
```

Behind that simple command, the code should read as one narrow product:

```text
Markdown units + donor Word slots -> proven alignment plan -> transplanted DOCX
```

That is the architecture. Not a renderer, not an IR backend, not a one-off
script.
