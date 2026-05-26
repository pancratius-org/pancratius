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
  schema. The body is editorial commentary (an SEO-targeted blog post about the
  video); if it is empty/short the page renders a compact layout, otherwise the
  book-like layout. Channels live in `src/content/videos/channels.yaml`;
  `uv run pancratius video sync` polls them and scaffolds new drafts
  mechanically (frontmatter + thumbnail only). See [Videos](#videos).

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

Multi-source works keep their original optimized source parts beside the work,
for example `ru-part1.docx`, `ru-part2.docx`, and `ru-part3.docx`. Those parts
are provenance and local editing assets. A public per-work DOCX download is
only the merged release artifact named `<lang>.docx`; if that file does not
exist, the site does not expose a `.docx` route for the merged work.

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

cover_is_placeholder: false         # optional; flips RU-cover fallback on EN
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

Most Markdown bodies are ordinary CommonMark prose:

- a single source newline inside a paragraph is just wrapping;
- a blank line starts a new paragraph;
- authors do not add trailing `\` or invisible two-space hard breaks.

Verse-like content is the deliberate exception. For `kind: poem` and the
manifesto page (`src/content/pages/mission/<lang>.md`), source lineation is content:

```md
Первая строка
Вторая строка

Следующая строфа
Ещё строка
```

The rule is:

- adjacent source lines are verse lines;
- a blank source line separates stanzas;
- no trailing `\`, no two-space ritual in source Markdown.

The website preserves this lineation-preserving register: the mission page
renders through the `<Verse>` component, while poems currently render the
`prose--poem` register directly (`class="prose prose--poem"`) — work-page bodies
are not yet on the shared `<Prose>`/`<Verse>` components (see
[`decisions.md`](./decisions.md)). Export code may add explicit hard-break
markers to downloadable Markdown scratch/output so strict CommonMark readers
preserve the same lineation, but those markers are not part of the author-facing
source.

Converters must preserve real stanza breaks. For DOCX poetry, the source signal
is Word paragraph structure: non-empty paragraphs are verse lines, empty
paragraphs are stanza breaks, and in-paragraph line breaks are verse lines inside
one stanza. The converter reads this through Pandoc's `docx+empty_paragraphs`
AST and writes the author-facing Markdown shape above. Do not run a blanket
`blank-line-between-every-line -> single newline` collapse over Pandoc's GFM
output; by then the stanza signal has already been blurred. The poetry stanza
audit must fail if converted Markdown no longer matches the DOCX stanza
structure.

The same source signal appears inside some books. Named sections such as
`Посвящение`, `Предисловие от Творца`, `Слово Творца`, and `Молитва` are clear
examples, but the rule is structural rather than name-only: when the DOCX AST
contains a confident run of short lineated lines, the converter emits an explicit
`<div class="verse-block">` for that run. A verse-block is a confident run of
short lineated lines — each line is ≤120 characters, and the run carries a
source-lineation signal: ≥2 lines when the signal is strong (a hard `<w:br/>`
line break, a heading, or a thematic separator), or ≥3 lines when the signal is
weak (lineation implied only by stanza-break empty paragraphs). Short colon
openers such as `Он говорил:` and `Разве не сказал Я:` stay inside the run;
explicit speaker/source turns such as `Панкратиус: ...` or `Ответ от Творца:`
end it. The wrapper contains natural source lines and blank stanza lines, not
hand-authored `<p>` / `<br>` markup. It is converter-owned output; authors are not expected to type this
HTML. CSS preserves that lineation while ordinary prose remains ordinary
Markdown paragraphs. Inline emphasis inside converter-owned HTML wrappers is
HTML (`<strong>`, `<em>`) because CommonMark does not parse `**...**` as
Markdown inside raw HTML blocks; public Markdown downloads may rewrite those
inline tags back to Markdown and add explicit hard-break markers so portable
Markdown readers preserve the lineation. If a numbered Q/A answer is lineated, it
uses the same `verse-block` contract.

DOCX paragraph metadata is also source data. Pandoc's Markdown writer does not
carry Word paragraph alignment, so the converter reads `word/document.xml`
directly for narrow semantic cases:

- right-aligned signature paragraphs become `<p class="signature">`;
- right-aligned scripture / epigraph groups become
  `<blockquote class="epigraph">`;
- a standalone `***` line (escaped or unescaped in Pandoc output) becomes a
  real Markdown thematic break.

Do not infer these from rendered CSS, italic-only paragraphs, or arbitrary
short-line runs. The signal must come from the DOCX structure or an explicit
source marker.

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

Videos are catalogued at `/videos/{slug}/` (RU) and `/en/videos/{slug}/` (EN).
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
description: |                           # SEO/OG/card; mandatory
  Single paragraph that opens the page and feeds search/cards.
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
layout: compact | blog                   # optional override; default = derived
translation:
  source: original
```

The compact-vs-blog layout is derived by `src/lib/video-format.ts:layoutFor`:
`compact` when the rendered body has zero headings and <600 characters of raw
text, `blog` otherwise. An explicit `layout:` field overrides the heuristic.

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
(`src/pages/<slug>/index.astro`, plus `/en/`) composing shared blocks
(`<Prose>`, `<Verse>`, `PageShell`); there is no generic slug-dispatching
renderer. A page's assets co-locate with it (about's portrait, support's QR), and
renderable images go through `astro:assets`; `public/` is only for files needing
a stable unprocessed URL.

## What Lives Where

| Lives in | What |
|----------|------|
| frontmatter | `kind`, `number`, `slug`, `title`, `lang`, `description`, `tags`, `cover`, `translation`, `cross_refs`, `cover_is_placeholder` |
| markdown body | the work itself, with relative links only to true inline body images |
| work folder assets | covers, true body illustrations, source DOCX/PDF |
| `bibliography.yaml` | long catalog/bibliography snapshots and external marketplace links |
| `data/` | generated corpus-wide data products (graph JSON) and two distinct provenance manifests, both outside any work bundle so bundles stay byte-identical on re-import: the importer's per-work `data/imports/<work-key>.json` (volatile run provenance — timestamps, source hashes; gitignored, never committed) and `docx_optimize.py`'s shared `data/conversion-manifest.json` (committed source-provenance index) |
| `public/` | static files intentionally published as-is, not authored work assets |

## Adding A New Work

1. Run the one-DOCX importer; it creates the work folder and writes the
   cleaned/optimized DOCX artifact into the bundle:

   ```sh
   uv run pancratius work import /path/to/new.docx --kind book --lang ru
   ```

   To add a translation to an existing work, target the existing bundle key:

   ```sh
   uv run pancratius work import /path/to/book-en.docx --into 30-poslanie-musulmanam --lang en
   ```

2. Add `--title`, `--number`, `--slug`, `--description`, or `--cover` when the
   importer cannot infer the desired value. Missing descriptions are seeded as
   an obvious `TODO:` value so the file validates but remains easy to find.
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
