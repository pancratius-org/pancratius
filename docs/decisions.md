# Data-Layer Decisions

This file records engineer-discretion choices made while making the data layer
production-ready. It is subordinate to `architecture.md` and `content-model.md`;
if a choice here conflicts with those contracts, update this file.

## Work-Bundle Asset Route

Authored image sources live in the work bundle, not in a parallel author-facing
`public/media/` hierarchy.

Reasons:

1. A non-technical author adding a book should work in one folder: Markdown,
   source DOCX/PDF, cover, and body illustrations together.
2. Covers are editorial assets, not anonymous hashed blobs. `cover.ru.jpg` and
   `cover.en.jpg` are easier to inspect, replace, and review than a separate
   hash path.
3. Body illustrations belong to the argument of a specific book. Keeping shared
   images directly under `images/`, and language-specific images under
   `images/<lang>/`, preserves that ownership without forcing an extra folder
   for the common case.
4. Converter-extracted inline DOCX images may keep short hash-derived filenames
   such as `images/649a499a5bdb.jpg`; after import these are stable asset IDs,
   not live checksums. Author-added images can use readable names.
5. Bibliography/reference thumbnails are not body illustrations. They become
   structured `bibliography.yaml` / `cross_refs` data, not Markdown image refs.
6. Deduplication and optimization are build/conversion concerns. The source
   layout should optimize for human editing; generated public asset paths can
   still be hashed in `dist/`.

The converter may still compute hashes internally and record them in
`data/conversion-manifest.json`, but `public/media/` is not the storage contract
for authored sources.

Body images have a work-bundle identity. The Markdown file decides which
`images/...` asset it references, but the original public URL is work-scoped:
`/assets/<kind-segment>/<work-key>/images/<file>`. It is not scoped to a
localized reading page or a per-language slug, so RU and EN markdown exports
share the same canonical original image URL when they reference the same file.
HTML reading pages still use Astro's image pipeline for rendered body images.

The same source-of-truth rule applies to reruns: the converter is additive by
default. It may refresh files it generated, but it must not wipe a work bundle
and thereby delete author-added images or editorial sidecars. A destructive
clean-room rebuild is a separate audit workflow that writes to scratch output or
deletes only manifest-owned files.

## Bibliography Sidecar

DOCX bibliography/catalog tables are stored as `bibliography.yaml`, not in
Markdown body and not in frontmatter.

```yaml
kind: catalog_snapshot
lang: ru
source: docx_endmatter
entries:
  - title: "Князь мира сего"
    source_url: https://www.litres.ru/72586354/
    target:
      kind: book
      number: 32
```

Rationale:

- Corpus samples show these lists are usually 20-80+ row catalog snapshots, not
  curated recommendations.
- Frontmatter should remain human-editable. An 81-row catalog makes the top of
  every Markdown file hostile.
- Website HTML already has `/books/` as the living catalog.
- EPUB/PDF can optionally append the sidecar as an archival author catalog.

## Reader-Facing Relations

Reader-facing book relations have two surfaces:

- **См. также** from authored `cross_refs`.
- **Похожие книги** from algorithmic recommendations.

Long bibliography/catalog snapshots do not feed either surface by default.
Algorithmic recommendations exclude authored `cross_refs` so the reader does not
see duplicate suggestions.

## Divine-Voice Marking

The converter preserves whatever italic markup pandoc emits from source DOCX
`<w:i/>` runs. It does not invent semantic emphasis from `Творец:` / `Бог:`
speaker labels.

Rationale:

- The contested claim that book #33 lost italic in "Предисловие от Творца" was
  checked against source XML and was false for that block.
- A heuristic that wraps every paragraph after a divine speaker label would
  over-fire on quoted prose, narration, and mixed dialogues.
- Corpus-wide divine-voice consistency is an editorial/source issue, not a safe
  converter transform.

## Downloadable DOCX

The downloadable `src/content/<kind>/<work>/<lang>.docx` is preserved from the
author's DOCX, then optimized and cleaned in place when needed:
rights-boilerplate removal and image re-encoding to the actual display
rectangle.

It is never regenerated from Markdown by default because a pandoc round-trip can
lose layout details that make the DOCX valuable as a source artifact.

## Downloadable PDF / EPUB

PDF and EPUB are committed release artifacts in the work bundle, not products of
the deploy build.

Rationale:

- A static library site should have a simple deploy path: validate, build,
  publish files.
- Document rendering brings heavyweight tools, fonts, templates, and format
  quirks. That is library-management work, best run explicitly and reviewed
  before commit.
- CI should not silently skip promised downloads because a runner lacks pandoc or
  typst, and it should not spend every deploy manufacturing hundreds of stable
  artifacts.
- Keeping `ru.pdf` / `ru.epub` beside `ru.md` makes the work bundle tell the full
  story of the work and keeps the author workflow understandable.

## Project Numbering

Projects carry a `number` (`enlightened-ai` = 1, `holy-rus` = 2) for editorial
identity only. Projects are themed sections, not works (see `content-model.md`),
so this number does NOT enrol them in the `(kind, number)` work-pair model, and
they carry no download matrix.

## ASCII Slug Folder Names

The converter emits `src/content/<kind>/<ascii-work-key>/<lang>.md`. Transliteration
is practical (`й → i`, `ц → ts`, hard/soft signs drop, lowercase only). Existing
Cyrillic folders are replaced by a re-conversion or cleanup migration; recurring
conversion must not recreate Cyrillic folders.

## Reading-Page Prose Styling

`src/styles/prose.css` is imported from `global.css` and applied wherever the
`<Prose>` / `<Verse>` components wrap Markdown-rendered body content — book and
poem pages, project landings and sub-pages, and the dedicated static-page routes.

It encodes a reading register designed for the *actual* markup our pandoc
pipeline emits, not for the hand-authored markup of the v4/v5/v7 mockups.
The corpus is largely short paratactic lines in Word that arrive as one
`<p>` per blank line, plus a small set of long narrative paragraphs. The
v7 register (justified text + 1.4em first-line indent + chapter-italic
`<h2>`) was authored against the mockup markup and broke on real markdown:
justify + indent on one-line paragraphs produces a staircase, and centred
italic `<h2>` mis-types Word "Heading 2" section labels as chapter openers.

The current contract:

- **ragged-right, no hyphens, no first-line indent.** Paragraph rhythm is
  vertical (`margin-bottom: 0.95em`), not horizontal indent. This reads
  as flowing prose on long paragraphs and as cleanly stacked short lines
  on paratactic ones, with no fake-Word inter-word gaps.
- **drop cap is opt-in only** via `<p class="lead">`. The corpus often opens
  with a dedication, dialogue, or short liturgical fragment; automatic drop
  caps mis-type those openings.
- **verse / divine-voice sections are explicit.** The converter reads the DOCX
  through Pandoc's `docx+empty_paragraphs` JSON AST, detects named and
  structural short-line runs, and emits a `<div class="verse-block">` containing
  natural source lines and blank-line stanza breaks. CSS styles that explicit
  structure. It does not guess verse from arbitrary italic paragraphs at render
  time, and authors are not asked to hand-write `<p>` / `<br>` line markup.
  Inline emphasis inside that raw HTML wrapper uses HTML tags because CommonMark
  does not parse `**...**` inside raw HTML blocks.
- **there is one lineated block shape.** Numbered sections, catechetical
  answers, prayers, and divine-voice short-line runs all use
  `<div class="verse-block">` when the source is lineated. The converter does not
  maintain a separate answer-wrapper taxonomy unless the site gives that register a
  genuinely different visual and semantic treatment.
- **right-aligned DOCX paragraphs are preserved as semantics only when the
  source makes the role clear.** Standalone author/source lines become
  `p.signature`; scripture and epigraph groups become
  `blockquote.epigraph`. This comes from `word/document.xml`, not from CSS
  heuristics or "italic means quote" guessing.
- **standalone `***` is a thematic break.** Pandoc sometimes escapes the
  asterisks; the converter normalizes escaped or unescaped `***`-only lines to
  a real GFM thematic break so the site renders the intended ornament.
- **`<h2>` is small-cap sans eyebrow in accent**, left-aligned with a
  hairline underline. It reads as Word "Heading 2", which is what mid-book
  section labels actually are. `<h3>` keeps a quiet italic-serif register
  for chapter sub-sections.
- **`<hr>` is an ornament rule.** Author-supplied `p.ornament` and
  `p.signature` classes are honoured if present.

### Body renderers: `<Prose>` and `<Verse>` (no "register" abstraction)

Two body-renderer components express the two registers — there is no `register`
enum and no slug-dispatcher. They take a `class` prop so a route can layer a
page-local modifier (`prose--bio`, `prose--svet`, `prose--project`); that is the
only per-page knob, and the component never branches on what the class means.

- **`<Prose>`** — flowing prose (the contract above): paragraph rhythm, opt-in
  drop cap, eyebrow `<h2>`.
- **`<Verse>`** — the lineation-preserving register (`white-space: pre-line`,
  left-aligned, no drop cap; `prose--manifesto` behavior). "Manifesto" was never
  a separate mode, only a label on the verse renderer.

**Scope (current):** these components are used by the **static pages and project
sub-pages** — the mission page is the one `<Verse>` user today. **Work pages
still render the prose register directly**: `BookPage.astro` emits
`class="prose"` and the poem route emits `class="prose prose--poem"`; they are
not on the shared components yet. Migrating book/poem bodies onto
`<Prose>`/`<Verse>` is a follow-up — the components were built so that adoption
is clean.

There is no generic `[slug].astro` modifier-picker (it was deleted). Each static
page is its own dedicated route that composes `<Prose>` or `<Verse>` and owns any
page-specific layout itself (see "Pages" in `content-model.md`). Page-specific
looks — about's portrait grid, support's widget, svetozar's treatment — live in
those routes/components, not as branches in a shared renderer.

If editorial wants slug-specific classes (`.lead`, `.signature`, `.ornament`)
they may be authored directly in the Markdown. The converter emits only
structural classes it can justify from source shape and section name, such as
`.verse-block`.

## Verse Source Contract

Poems and the manifesto use natural source lineation rather than Markdown hard
break syntax:

- adjacent source lines are displayed as verse lines;
- blank source lines are stanza breaks;
- source Markdown does not use trailing `\` or invisible two-space breaks.

This is an authoring decision, not just a renderer trick. A non-technical author
should be able to paste or write a poem as a poem and see the same lineation on
the site. The web layer implements it with the `<Verse>` component
(`white-space: pre-line` on paragraphs), while `<Prose>` keeps normal CommonMark
semantics.

Portable exports may add explicit hard-break markers in generated `.md` or use
Pandoc's hard-line-break parsing for PDF/EPUB scratch files. Those markers do
not belong in source content.

The converter must treat stanza boundaries as editorial data. DOCX poems and
book lineation are read through Pandoc's `docx+empty_paragraphs` JSON AST,
because empty Word paragraphs survive there as explicit empty paragraph nodes.
Poems emit the simple source contract above; book sections and confident
short-line runs emit a minimal `.verse-block` wrapper around natural lines so
stanza structure is explicit on normal prose pages. A confident run is short
lineated lines (each ≤120 characters) carrying a source-lineation signal: ≥2 lines
on a strong signal (a hard `<w:br/>`, heading, or thematic separator), else ≥3
lines on the weak empty-paragraph-only signal. Short colon openers remain inside
the lineated run; explicit speaker/source turns end it. The converter must not
infer stanza structure from plain Pandoc GFM after the empty-paragraph signal has been
lost, and it must not silently flatten all poems into one stanza. The 120-char
threshold is duplicated in `pancratius/ir/normalize.py` (`VERSE_SHORT_LINE_MAX`)
and `audit/book_verse.py` (`SHORT_LINE_MAX`) — the audit is the
independent DOCX-source oracle for the IR rule, so the two values must stay in
sync.

## Conceptosphere page-layout selectors are global

The conceptosphere pages set `class="cs-main"` on `<main>` via the Base
layout's `mainClass` prop. Astro's scoped-style attribute is added to the
page's own template, not to the layout slot, so a scoped `.cs-main { … }`
rule never matches the live `<main>`. Phase 6 ships those rules with explicit
`:global(.cs-main)` selectors; the same pattern applies for any future page
that needs to style the slotted `<main>` from the layout.

## Python type enforcement: ruff + ty (Astral), not mypy/pyright

The architecture mandates type-hinted Python. Two enforcement axes map to two
tools: **ruff** (flake8-annotations `ANN` ruleset) enforces that annotations are
*present*; **ty** (Astral's type checker) adds best-effort static type checking.
The honest claim is **annotation coverage plus best-effort checking, not total
type soundness** — the `replace-imports-with-any` list (below) deliberately treats
several un-stubbed libraries as `Any`. Both tools are dev-dependencies pinned in
`uv.lock` and run via `uv run` (`npm run check:py`, and a `--frozen` step in CI).

Why ty over the conventional pyright/mypy:

- Single-vendor Astral stack (ruff + ty + uv) — one toolchain, one config home,
  fast.
- The repo uses **zero** mypy plugins (no Django/Pydantic/SQLAlchemy) — the one
  area where mypy is still irreplaceable does not apply here. The Python is pure
  stdlib plus scientific/ML libraries.
- ty's pre-1.0 risk is missed checks (false negatives), not false positives that
  wrongly block CI; annotation *presence* is held by ruff regardless. In
  practice ty already caught real latent bugs here (a wrong `slug_lookup` dict
  type and an `int`/`float` reassignment), so it earns its place.

Caveat and exit: ty is pre-1.0, so it is pinned **exact** (`ty==`), not `>=`.
Re-evaluate against pyright/mypy `--strict` when ty reaches a stable line. `Any`
is permitted only at genuine dynamic boundaries (opaque ML model/tokenizer
objects, untyped Pandoc-AST payloads, un-stubbed third-party imports) and must be
explicit through local aliases or narrow typed payload edges — never a blanket
suppression.

## Import is the publish gate: harden authored content, not the renderer

The site's Markdown renderer carries no sanitizer — verse-blocks, signatures, and
bidi spans are converter-emitted raw HTML the pages depend on, so a sanitizer
can't be added without breaking them. The import is therefore the single gate:
literal authored text must not become unintended active markup in published pages.
The importer accordingly escapes literal `Text` (Markdown/HTML metacharacters,
variable-length code fences), allowlists link/image URL schemes
(http/https/mailto/relative; unsafe schemes dropped with a diagnostic), and makes
an unresolvable or scope-escaping local image a fatal write-refusal.

Imported **body-image SVGs** are sanitized at the writer's copy boundary (strip
`<script>`, `on*` handlers, `javascript:`/`data:` hrefs, `<foreignObject>`,
external refs) because they are served raw, same-origin. **Covers are
deliberately excluded** from this sanitize: they are admin-curated design assets
on a different trust path (committed directly or passed via an explicit
`--cover`), and the author cover SVGs legitimately use `<foreignObject>` to render
their styled title — sanitizing would corrupt them. The committed body SVGs are
clean (the sanitizer is a byte-for-byte no-op on them); the scoping lives in
`pancratius/writer.py`.
