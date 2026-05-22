# Pancratius Content Model

The **storage contract** for works in this corpus. Astro content collections,
downloads, search, and graph data all read from this shape.

The model has one product goal: a work folder should tell the whole story of
that work. The author or an assisting agent should not have to juggle a content
tree, a parallel media tree, and hidden metadata files to add one book.

> This document is the storage shape, not the author workflow. Author edits
> happen through Markdown files directly, Codex-assisted scripts, or a future
> small UI; that flow gets its own doc when it exists.

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
      meta.json             # temporary conversion audit; not UI truth
  poetry/
    01-a-esli-budu-ya-ne-prav/
      ru.md
      ru.docx
  projects/
    enlightened-ai/
      ru.md
      ru.docx
      cover.ru.jpg
```

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
Conversion scripts must be additive by default: update files they own, preserve
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
kind: book                          # "book" | "poem" | "project"
number: 1                           # mandatory across all kinds; invariant identity
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

`number` is mandatory on every kind, including projects. The corpus has **one
invariant identity rule**: `(kind, number)`. Projects today are numbered 1
(`enlightened-ai`) and 2 (`holy-rus`) — the numbering is editorial, not
URL-bearing.

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

The website preserves this with the `.prose--poem` and `.prose--manifesto`
rendering classes. Export code may add explicit hard-break markers to
downloadable Markdown scratch/output so strict CommonMark readers preserve the
same lineation, but those markers are not part of the author-facing source.

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
contains a confident run of short lineated paragraphs, the converter may emit
an explicit `<div class="verse-block">` for that run. The wrapper contains
natural source lines and blank stanza lines, not hand-authored `<p>` / `<br>`
markup. It is converter-owned output; authors are not expected to type this
HTML. CSS preserves that lineation while ordinary prose remains ordinary
Markdown paragraphs. Inline emphasis inside converter-owned HTML wrappers is
HTML (`<strong>`, `<em>`) because CommonMark does not parse `**...**` as
Markdown inside raw HTML blocks; public Markdown downloads may rewrite those
inline tags back to Markdown.

Numbered Q/A books have a related but distinct shape: a numbered question
heading followed by a compact run of short answer paragraphs. That is not
automatically poetry, so the converter may emit `<div class="answer-block">`
for the answer run instead of forcing it into verse styling. The source signal
is the numbered question heading plus the adjacent short DOCX paragraphs.

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

Translations pair by **`(kind, number)`** across all kinds. One rule, no
exceptions.

The pair-by-shared-key rule lives in `src/lib/i18n.ts`. Routes never recompute
it ad hoc.

## What Lives Where

| Lives in | What |
|----------|------|
| frontmatter | `kind`, `number`, `slug`, `title`, `lang`, `description`, `tags`, `cover`, `translation`, `cross_refs`, `cover_is_placeholder` |
| markdown body | the work itself, with relative links only to true inline body images |
| work folder assets | covers, true body illustrations, source DOCX/PDF |
| `bibliography.yaml` | long catalog/bibliography snapshots and external marketplace links |
| `meta.json` | temporary conversion audit/provenance; read by scripts, not UI |
| `data/` | generated corpus-wide data products, such as graph JSON |
| `public/` | static files intentionally published as-is, not authored work assets |

## Adding A New Work

1. Run the one-DOCX importer; it creates the work folder and writes the
   cleaned/optimized DOCX artifact into the bundle:

   ```sh
   uv run scripts/import_docx.py /path/to/new.docx --kind book --lang ru
   ```

   To add a translation to an existing work, target the existing bundle key:

   ```sh
   uv run scripts/import_docx.py /path/to/book-en.docx --into 30-poslanie-musulmanam --lang en
   ```

2. Add `--title`, `--number`, `--slug`, `--description`, or `--cover` when the
   importer cannot infer the desired value. Missing descriptions are seeded as
   an obvious `TODO:` value so the file validates but remains easy to find.
3. Author edits `description`, `title`, `tags`, and `cross_refs` if needed.
4. Run the remaining local release-artifact scripts for the changed work:
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

The converter should emit the final shape natively. A one-time migration script
may clean old folders, rewrite `slug:` fields, and emit `data/slug-migration.json`,
but recurring conversion must not recreate Cyrillic folders or legacy metadata.

Projects were already ASCII-slugged from the start.
