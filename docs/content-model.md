# Pancratius Content Model

The **storage contract** for the corpus. Astro content collections, downloads,
search, and graph data all read from these shapes.

> This document is the storage shape, not the author workflow. The author/agent
> workflow and the command surface are in [`tooling.md`](./tooling.md).

## Content types (the ontology)

Four kinds of content with different identity rules — do not collapse them:

- **Works — books and poems — are a *population*.** Interchangeable in
  structure: one frontmatter shape, one renderer, sorted by `number`, shown in a
  card grid, paired across languages by `(kind, number)`. They are content
  *collections* and own the full download matrix. Most of this doc is about works.
- **Pages are *individuals*.** `about`, `mission`, `svetozar`, `license`,
  `support`, `downloads` — each a one-of-a-kind route with its own purpose, not a
  member of a population. They share a minimal `pages` schema + composable
  blocks, but each is its own dedicated route. See [Pages](#pages).
- **Projects are *themed sections* (mini-sites), not works.** A landing + ordered
  sub-pages, curated references into the library, their own visual identity. They
  do NOT flow through the work/download machinery. See [Projects](#projects).
- **Videos are a *catalogued population* at /videos/.** Paired across languages
  by `(kind, number)` like works, but NOT works: no DOCX-import flow, no
  PDF/EPUB/DOCX download matrix. Each video carries an ordered list of mirror
  URLs (`sources[]`) so the library survives any one platform pulling content;
  YouTube is the default platform today but has no privileged status in the
  schema. The body is a reading (an SEO-targeted blog post about the video); if it
  is empty/short the page renders a compact layout, otherwise the book-like
  layout. Channels live in `src/content/videos/channels.yaml`;
  `uv run pancratius video sync` polls them, and for each new video splits the raw
  YouTube description into a clean hook (`description`) and reading `body` — a
  faithful, QA-gated draft of the author's own words, not a raw dump. See
  [Videos](#videos).

The product goal for works: one folder tells the whole story of that work — no
parallel media tree, no hidden metadata files to add one book.

## Work Bundle

One folder is one work. The folder name is the canonical Russian ASCII work key:
author-facing in the file tree, never the public URL. Public routes are built
from each language file's frontmatter `slug`.

```txt
src/content/
  books/
    01-evangelie-tsarstviya/
      ru.md
      en.md                 # optional translation
      ru.docx               # source / downloadable artifact for RU
      en.docx               # optional translation artifact
      ru.pdf                # optional committed release artifact
      ru.epub               # optional committed release artifact
      cover.ru.jpg          # canonical RU cover master
      cover.en.jpg          # optional localized cover master
      images/
        649a499a5bdb.jpg    # converter-imported inline DOCX image
        ladder.jpg          # human-named body illustration used by any language
        ru/
          diagram-01.png    # language-specific body illustration
      bibliography.yaml     # optional export/provenance sidecar
  poetry/
    01-a-esli-budu-ya-ne-prav/
      ru.md
      ru.docx
```

(Projects partly live under `src/content/projects/` too, but follow the
[Projects](#projects) section's shape — a section, not a work bundle.)

Each work language has at most one active source DOCX: `<lang>.docx`. If an
author supplies a work in parts, merge those parts into one source DOCX before
committing the work bundle. The merged source must carry real part headings in
the DOCX outline; `uv run pancratius docx merge ... --part ...` inserts those
headings during the physical merge. The site exposes a `.docx` route only when
that single source DOCX exists beside the Markdown.

The important rule: **authored and release assets live with the work**. Covers,
body illustrations, source DOCX, and release downloads belong beside the
Markdown. Build output may still emit optimized public files into `dist/`, but no
human should maintain a separate `public/media/` hierarchy by hand.

The work bundle is persistent source content, not disposable build output.
Conversion tools must be additive by default: update files they own, preserve
unknown author-added neighbors, and never begin a normal re-conversion by
deleting `src/content/books`, `src/content/poetry`, or `src/content/projects`.
Clean rebuilds must be scoped to selected work folders, not whole content-kind
directories.

### Asset Naming

- Covers are never anonymous hashes. Use stable names such as `cover.ru.jpg` and
  `cover.en.jpg` because they are editorial assets an author can inspect and
  replace.
- Converter-extracted inline images from DOCX may use short content-derived
  names such as `images/649a499a5bdb.jpg`. In Markdown they are still ordinary
  relative links: `![...](./images/649a499a5bdb.jpg)`.
- After first import, a converter-created hashed filename is a **stable imported
  asset ID**, not a live integrity rule. If someone edits the file in place, the
  Markdown reference should keep working; the converter must not rename already
  referenced body images just because the bytes changed.
- Author-added body images may use readable names such as
  `images/ladder.jpg` or `images/ru/diagram-01.png`.
- Bibliography/reference thumbnails are not body assets. If a DOCX embeds covers
  of other works in a catalog table, lift that table into `bibliography.yaml` or
  authored `cross_refs`; do not keep those thumbnails as Markdown images.

## Work Frontmatter

```yaml
kind: book                          # "book" | "poem"  (works only; see Projects/Pages)
number: 1                           # mandatory for works; invariant identity
slug: 01-evangelie-tsarstviya       # per-language; drives the URL
title: Евангелие Царствия           # per-language string, never {ru, en}
lang: ru
description: |                      # mandatory; SEO / OG / card copy
  Краткое описание для поисковиков и карточек на /books/.
tags: [Откровение Бога, Библия]
cover: ./cover.ru.jpg               # relative to this work folder

translation:                        # required; originals use source: original
  source: original | literary | ai
  model: <model_id>                 # optional; recorded when known for AI translations
  generated_at: 2026-04-21          # optional
  reviewed_by: <name>               # optional

cross_refs:                         # optional; authored references only
  - target:
      kind: book
      number: 32
    source: footnote | inline_url | inline_title | editorial
    snippet: "Подробнее об этом Творец рассказывает в книге «Князь мира сего»."
    source_url: https://www.litres.ru/72586354/   # optional external source
```

`number` is mandatory on every **work** (book/poem) and is the invariant
identity rule: `(kind, number)` pairs a work across languages. Projects also
carry a `number` for editorial identity, but they are sections, not works —
they do not enter the work-pair / download machinery (see [Projects](#projects)).

`description` is mandatory and the only long-form metadata; SEO, cards, and
in-page openers all read it. Don't add a separate "abstract" field — keep the
description load-bearing.

`translation.source: ai` is the honest signal that an EN translation came from
a model; EN work pages surface it as a small "machine translation" line near
the colophon, with a link back to the RU original. No separate fallback flag
is needed.

## Markdown Body Contract

Markdown here is a derived *publication format*, not an authoring surface — an AI
generates it from a DOCX/text source; nobody hand-types it line by line. So ONE
uniform lineation encoding holds across the ENTIRE corpus and every page (books,
poems, the mission/manifesto page, project verse subpages), with no
authored-vs-derived distinction. An inconsistent encoding split across sections is
exactly what breaks the AI agents and humans this format serves.

The lineation encoding inside an explicit lineated wrapper:

```md
<div class="lineated">

Первая строка␣␣
Вторая строка

Следующая строфа␣␣
Ещё строка

</div>
```

- flowing prose has no breaks (a single source newline is just wrapping);
- a lineated line ends with **two trailing spaces** (the cross-consumer hard break
  that survives Astro, pandoc PDF/EPUB, and the public-Markdown export — a
  backslash break does not);
- a blank line separates stanzas; the final line of a stanza carries no break;
- a `***` line is a thematic/verse separator (CommonMark `<hr>`). It is NOT
  `---`, which under a text line parses as a setext heading in Astro.

The breaks render as `<br>`, so the verse CSS does NOT use `white-space: pre-line`
anywhere — CSS never infers lineation from a raw newline. REGISTER (prose voice vs
verse voice) is separate from lineation: book lineated prose is emitted as
`<div class="lineated">`, while book verse register is additive:
`<div class="lineated verse">` around the same line/stanza structure. Poems
(whole-body verse, no wrapper) get register from `kind: poem` / the poem
component; the mission page and project verse subpages get it from the `<Verse>`
component (`weight: verse`). A guard audit (`audit/lineation_breaks.py`,
PAN006B-lineation-breaks) fails if generated lineation — lineated wrappers,
poem, mission page, or verse subpage — loses its two-space breaks, the failure mode if a formatter trims `.md`
trailing whitespace, so `.editorconfig` carries
`[*.md] trim_trailing_whitespace = false`.

Converters must preserve real stanza breaks. For DOCX poetry, the source signal
is Word paragraph structure: non-empty paragraphs are verse lines, empty
paragraphs are stanza breaks, and in-paragraph line breaks are verse lines inside
one stanza. The converter reads this through Pandoc's `docx+empty_paragraphs`
AST and writes the generated-Markdown lineation shape above (two-space breaks
within a stanza, blank line between stanzas). Do not run a blanket
`blank-line-between-every-line -> single newline` collapse over Pandoc's GFM
output; by then the stanza signal has already been blurred. The poetry stanza
audit must fail if converted Markdown no longer matches the DOCX stanza
structure.

The same source signal appears inside some books. Named sections such as
`Посвящение`, `Предисловие от Творца`, `Слово Творца`, and `Молитва` are clear
examples, but the rule is structural rather than name-only. Import makes two
decisions: first preserve source lineation as line/stanza structure, then promote
only confident verse-register runs from `class="lineated"` to
`class="lineated verse"`. A lineated run contains natural source lines (two-space
hard breaks within a stanza, blank stanza lines) and no hand-authored `<p>` /
`<br>` markup. A blank line after the opening `<div>` lets CommonMark parse the
inner content, so lineation is still the two-space hard break and inline emphasis
is Markdown `**`/`*` (not raw HTML `<strong>`/`<em>`). The `<br>` the breaks
produce carries the lineation, so the verse register's CSS does NOT use
`white-space: pre-line`. Public Markdown downloads strip the lineated wrapper and
register while keeping the two-space breaks so portable readers preserve
lineation. If a numbered Q/A answer is lineated, it may remain
`class="lineated"` unless the import stage has separate high-confidence
verse-register evidence.

The importer's lineation reading is structural inference and can be wrong on
genuinely ambiguous paragraphs. A human-adjudicated correction is committed as a
per-book sidecar, `lineation.<lang>.json` beside `<lang>.docx`, keyed by source
paragraph ordinal with the adjudicated register (`prose` | `lineated`) and a
content hash of the paragraph text the verdict was made against. Import honors
the sidecar; the hash is a rail, never advisory — if the source text drifted
under a correction, import fails rather than apply or silently skip a stale
verdict. Only the `prose` direction is appliable today; a `lineated` entry fails
the import loudly rather than being ignored. Poems take no sidecar (their whole
body is verse; a sidecar beside a poem docx fails the import). The sidecar is a
projection of the research label store into content (labels and sidecar move
together, like docx and md); it never encodes style, only the register verdict.

Unmarked canonical quotations follow the same sidecar pattern on the scripture
axis. Most scripture is recognized structurally at import (borders, citation
formulas), but some canonical quotes carry no marker at all and are
recognizable only by knowing the canonical texts themselves. A
source-adjudicated verdict is committed as `scripture.<lang>.json` beside
`<lang>.docx`, keyed by source paragraph ordinal with the named canonical
source (e.g. `Откр 3:11`) and a content hash of the paragraph text. Import
wraps pinned paragraphs as scripture quote blocks; the hash is a rail (drift
fails the import), and a pin that no longer lands on a top-level prose
paragraph fails loudly rather than dissolve. Poems take no scripture sidecar.

DOCX paragraph metadata is also source data. Pandoc's Markdown writer does not
carry Word paragraph alignment or paragraph borders, so the converter reads
`word/document.xml` directly for narrow semantic cases:

- right-aligned signature paragraphs become `<p class="signature">`;
- right-aligned scripture / epigraph groups become
  `<blockquote class="epigraph">`;
- a standalone `***` line (escaped or unescaped in Pandoc output) becomes a
  real Markdown thematic break.

Do not infer these from rendered CSS, italic-only paragraphs, or arbitrary
short-line runs. The signal must come from the DOCX structure or an explicit
source marker.

Quotation marks are locale typography applied at import, not hand-edited into the
derived Markdown (which a re-import would revert). Russian text uses guillemets
(`«…»`); English text uses American curly double quotes (`“…”`). The rule covers
both a parsed `Quoted` inline and a literal guillemet typed into English source
(English has no guillemets, so it is a mistyped quote normalized to the same curly
double). Single quotes follow the same locale split.

### Display registers (set-apart blocks)

Beyond lineation, the author sets some passages APART from the running body
with paragraph borders (`w:pBdr`). The gesture kind carries the register, and
both are within-book contrastive: a border kind covering a large share of a
book's paragraphs is that book's own frame (a template choice) and lowers as
ordinary content, never as a register.

- a contrastive **full-box** run — quoted canonical text (scripture, logia,
  numbered verses) — becomes `<blockquote class="scripture">` (a raw wrapper
  whose inside is parsed Markdown, like the lineated wrapper; authored bold
  and refs survive);
- a contrastive **left-rule** run — an inset passage in a voice or provenance
  distinct from the body (dictation, framed reflection, commentary) — becomes
  a plain Markdown blockquote (`>`), unifying with the Word Quote-style
  channel Pandoc already lowers to `>`; member paragraphs stay distinct
  (separated by a bare `>` line) and authored hard breaks inside members stay
  two-space display lines.

Register is a parallel axis to lineation, not a sub-question of it: most
set-apart passages in the corpus are prose, not lineated. The existing
`signature` / `epigraph` / `ornament` classes and the `verse` lineation
register stay separate; a passage gets ONE primary register.

## Relations

Not every list of books means the same thing. The storage model keeps provenance
separate so the UI can stay simple.

### `cross_refs`

`cross_refs` are authored signals: the author explicitly points to another work in a
sentence, footnote, or editorial note. They are the only relation field that
belongs in frontmatter because the list is short, meaningful, and worth editing
by hand.

Reader UI: render as **«См. также»** only when non-empty.

### `bibliography.yaml`

Long DOCX bibliography/catalog tables do **not** belong in Markdown body or
frontmatter. In the current corpus they are usually catalog snapshots: 20-80+
works with LitRes links, often "all books known at publication time," not a
curated recommendation list.

Some sources store the same catalog as screenshots / pasted cover-grid images.
Those images are also bibliography, not body illustrations. The converter drops
them from the reading body and must not copy them into `images/`.

Keep them as an optional sidecar:

```yaml
kind: catalog_snapshot
lang: ru
source: docx_endmatter
entries:
  - title: Князь мира сего
    source_url: https://www.litres.ru/72586354/
    target:
      kind: book
      number: 32
```

Use cases:

- provenance and audit;
- optional EPUB/PDF appendix if preserving the source endmatter matters;
- external marketplace links where they still have archival value.

Do **not** render `bibliography.yaml` as the normal website "read next" widget.
The website already has `/books/` as the living catalog.

### Algorithmic Similarity

Algorithmic similarity is not stored in work frontmatter. It is generated into
`data/pancratius-books-graph.json` and consumed by the graph page and book pages.

Reader UI: render as **«Похожие книги»**, excluding the current work and anything
already shown in `cross_refs`.

## Cross-Language Pairing

Works (books and poems) pair across languages by **`(kind, number)`** — one
rule, no exceptions. The pairing lives in `src/lib/works.ts` (`WorkPair.entries`
keyed by locale); routes never recompute it ad hoc. Projects and pages are not
paired works: a localized project/page exists only when that locale is authored
(see [`i18n-routing.md`](./i18n-routing.md)).

## Projects

Projects are **themed sections**, not works. A project is a landing + ordered
sub-pages under `src/content/projects/<slug>/`:

- `ru.md` — the landing. Frontmatter is a *section descriptor*: `kind: project`,
  `slug`, `number` (editorial identity only), `title`, `tagline`, `description`,
  `cover`, `theme`, `featured_books` (library books referenced by `number`),
  `subpages` (ordered, with `weight` labels), optional `revelations`/`faq`. Body
  is the landing prose.
- `subpages/<sub>/ru.md` — a sub-page: `kind: project_subpage`, `parent`, `slug`,
  `weight` (essay/revelation/verse/practice/dialogue), `title`, `description`,
  optional `cover`/`component` (a bespoke interactive component).

Projects have **no download matrix** and are excluded from `all-md.zip`. They are
**converter-excluded** — the importers refuse `project` as a kind — because
project content is authored, not converted: a new sub-page is *scaffolded*
mechanically then *composed* editorially (see [`tooling.md`](./tooling.md)). A
document worthy of the library is **promoted to a real book** (its own
`(kind, number)`); a project then references it via `featured_books`, never
copies it.

Note: project landings still appear in the build-time route manifest
`data/slug-map.json` (under its `entries` array, keyed via `SEGMENT_OF`) so the
sitemap emits their URLs and hreflang. That manifest is a *route index*, not the
`WorkPair` model — do not remove projects from it.

## Videos

Videos are catalogued at `/ru/videos/{slug}/` (RU) and `/en/videos/{slug}/` (EN).
One folder per video, mirroring the work-bundle shape:

```txt
src/content/videos/
  channels.yaml                          # authored sidecar (platforms + copy)
  01-evangelie-glava-1/
    ru.md                                # frontmatter + commentary
    en.md                                # optional locale commentary
    cover.ru.jpg                         # thumbnail (e.g. YouTube maxres)
    cover.en.jpg                         # optional localized cover
    images/                              # optional inline blog-post images
```

Frontmatter shape (zod-validated):

```yaml
kind: video
number: 14                               # invariant identity; pairs across locales
slug: 14-jacob-and-esau                  # per-locale ASCII slug
title: "Jacob & Esau — what the story really says"
lang: en
description: |                           # mandatory; editorial copy (SEO / cards / OG)
  Authored copy describing the video.
tags: [Bible, Apocalypse]                # like books; the scanner seeds these
                                         # from YouTube playlist titles.
cover: ./cover.en.jpg                    # optional; falls back to RU
published_at: "2026-01-22"               # ISO date; source publication
duration: "PT8M42S"                      # ISO 8601 (matches YouTube)
sources:                                 # ordered: first = primary, others = mirrors
  - platform: youtube
    id: "abc123XYZ"
    url: "https://www.youtube.com/watch?v=abc123XYZ"
    embed_url: "https://www.youtube-nocookie.com/embed/abc123XYZ"
    channel: main                        # ref into channels.yaml `id`
  - platform: vimeo                      # future mirror
    id: "987654321"
    url: "https://vimeo.com/987654321"
playlists:                               # optional; from YouTube
  - id: "PLFvJf-...XjmgPh3CySk"
    title: "Апокалипсис"
related_book: 1                          # optional cross-link to a book
translation:
  source: original
```

Every video renders through one layout: a single reading column (masthead, embed,
meta, then the commentary). The written commentary is optional — when the body is
empty the page simply omits that final section; the chrome above it never changes,
so a catalogue entry and a full essay open identically.

Channels live in a sidecar so the same file feeds two consumers:

```yaml
# src/content/videos/channels.yaml
- id: main
  platform: youtube
  handle: "@pankratyus"
  channel_id: ""                          # cached after first scan
  url: "https://www.youtube.com/@pankratyus"
  title: { ru: "Основной канал",  en: "Main channel" }
  copy:  { ru: "...",             en: "..." }
  scan: true                              # `scan: false` = catalogue-only
```

Videos are excluded from `all-md.zip` (no download matrix) and have no
`[slug].[format].ts` endpoints. Pagefind indexes blog-layout video bodies via
the standard `<article data-pagefind-body>` wrapper.

## Pages

Static pages live in the `pages` collection
(`src/content/pages/<slug>/<lang>.md`) but are **individuals**, not a population.
The shared schema is minimal — `slug`, `lang`, `title`, `description`,
`eyebrow?`, `sub?` — and uses zod `.loose()` so a page may carry bespoke
frontmatter (about's `portrait`/`facts`, support's `channels`) that its own
dedicated route validates and renders. Each page is a thin dedicated route
(`src/pages/ru/<slug>/index.astro`, plus `/en/`) composing shared blocks
(`<Prose>`, `<Verse>`, `PageShell`); there is no generic slug-dispatching
renderer. A page's assets co-locate with it (about's portrait, support's QR), and
renderable images go through `astro:assets`; `public/` is only for files needing
a stable unprocessed URL.

## What Lives Where

| Lives in | What |
|----------|------|
| frontmatter | `kind`, `number`, `slug`, `title`, `lang`, `description`, `tags`, `cover`, `translation`, `cross_refs` |
| markdown body | the work itself, with relative links only to true inline body images |
| work folder assets | covers, true body illustrations, source DOCX/PDF |
| `bibliography.yaml` | long catalog/bibliography snapshots and external marketplace links |
| `data/` | generated corpus-wide data products (graph JSON) plus importer per-work `data/imports/<work-key>.json` volatile run provenance (timestamps, source hashes; gitignored, never committed), outside any work bundle so bundles stay byte-identical on re-import |
| `public/` | static files intentionally published as-is, not authored work assets |

## Adding A New Work

1. Run the one-DOCX importer; it creates the work folder and writes the
   cleaned/optimized DOCX artifact into the bundle:

   ```sh
   uv run pancratius work import /path/to/new.docx --kind book --lang ru
   ```

   To add a translation to an existing numbered work, target its selector:

   ```sh
   uv run pancratius work import /path/to/book-en.docx --to book:30 --lang en
   ```

2. Add `--title`, `--to`, `--slug`, `--description`, or `--cover` when the importer
   cannot infer the desired value. Missing descriptions are seeded as an obvious
   `TODO:` value so the file validates but remains easy to find.
3. Author edits `description`, `title`, `tags`, and `cross_refs` if needed.
4. Run the remaining local release-artifact tools for the changed work:
   render PDF/EPUB and refresh bulk Markdown.
5. Re-run graph generators so algorithmic recommendations include the work.

If a fully reproducible clean conversion is needed for audit, run it into a
scratch output directory or use an explicit destructive mode that removes only
converter-owned files recorded in the manifest. Do not make destructive cleanup
the default authoring workflow.

## Migration: Cyrillic → ASCII Slugs

The corpus historically used Cyrillic-slug folders. The final model uses ASCII
folder keys and ASCII public slugs. Transliteration is practical, not GOST 7.79:
`ц → ts`, `й → i`, soft and hard signs drop, lowercase only. Number prefix is
preserved.

The converter should emit the final shape natively. A one-time migration tool
may clean old folders, rewrite `slug:` fields, and emit `data/slug-migration.json`,
but recurring conversion must not recreate Cyrillic folders or legacy metadata.

Projects were already ASCII-slugged from the start.
