# Translated DOCX Transfer

This package owns:

```sh
uv run pancratius docx translate-from-md [book:NN] --lang en
```

It bootstraps a translated source DOCX from three committed files in one book
bundle:

- `ru.docx`: donor Word package and layout authority;
- `ru.md`: imported source Markdown used as the alignment key;
- `en.md`: translated Markdown supplying target text, links, and footnotes.

The command writes `en.docx`. The site build never creates or repairs that file.

## Contract

This is a transfer pipeline, not a translator and not a generic Markdown DOCX
renderer. The donor DOCX owns Word structure: paragraphs, runs, drawings,
relationships, footnote mechanics, cover fields, and layout intent. Markdown
supplies text only after source and target units align.

Alignment failures are terminal diagnostics, not repair prompts. If source DOCX
and Markdown cannot be matched, fix the corpus input or use the explicit
`--backend markdown-render` fallback for that book.

Before a translated DOCX exists, `en.md` is the bootstrap translation artifact.
After `en.docx` exists and is accepted, DOCX is source. Batch mode creates only
missing translated DOCX files; replacing one requires `book:NN --replace`.
Corpus-wide replacement is refused.

Footnotes are assigned by body anchor order. The writer preserves donor
`w:footnoteReference` IDs in `word/document.xml` and maps translated note bodies
onto those IDs by first appearance, not by `word/footnotes.xml` ordering. Missing,
duplicated, or unreferenced body IDs fail the transfer.

## Modules

- `models.py`: bounded-context types and diagnostics.
- `markdown_units.py`: Markdown AST extraction into transfer units.
- `donor_docx.py`: donor package reads and Word text slots.
- `align.py`: source/target unit pairing and Word-slot alignment.
- `ooxml_write.py`: deterministic OOXML mutation and package writing.
- `transfer.py`: one-book render paths.
- `batch.py`: target discovery, writer plans, and replacement rules.
- `report.py`: human and JSON summaries.
- `audit.py`: committed translated DOCX artifact checks behind PAN025.
- `pipeline.py`: compatibility facade for older internal imports.

## Gates

Transfer writes staged bytes, validates the OOXML package, then commits through
the normal writer plan. PAN025 checks committed artifact shape through:

```sh
npm run audit:repo
```

That gate reads committed DOCX packages directly and checks that footnote tables
are valid and drawing name, description, and title metadata contains no Cyrillic.
After changing English DOCX artifacts or import/transfer code, run
`uv run pancratius docx roundtrip-md --lang en`.
