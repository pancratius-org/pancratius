# Pancratius Downloads

Per-work downloads are alternate representations of the work URL. Bulk archives live at `/downloads/`.

The clean boundary is:

- **Library management is local/admin work.** Import DOCX, optimize DOCX, render PDF/EPUB, refresh graph data, and review editorial metadata before committing.
- **CI builds and publishes the site.** It validates the committed library, emits HTML/search/sitemap/download routes, and deploys `dist/`. It does not run pandoc, typst, embedding models, or DOCX optimizers.

## Per-work downloads

A work URL `/books/{slug}/` (or its English equivalent) has alternate representations addressable by extension:

| Extension | Source | Generation |
|-----------|--------|------------|
| `.md` | the work's `*.md`, frontmatter stripped; verse lineation made portable where needed | trivial |
| `.txt` | the markdown body, Markdown syntax flattened | trivial |
| `.docx` | the committed sibling `<lang>.docx` | file copy |
| `.pdf` | the committed sibling `<lang>.pdf` | file copy |
| `.epub` | the committed sibling `<lang>.epub` | file copy |

Each format is delivered by a single Astro static endpoint per kind per locale:
`[slug].[format].ts`. `getStaticPaths` enumerates only formats that exist for the
work. The endpoint delegates to `src/lib/downloads.ts` for dispatch and bytes.

## Release Artifacts

PDF, EPUB, and merged multi-part DOCX files are **release artifacts** stored in
the work bundle:

```txt
src/content/books/01-evangelie-tsarstviya/
  ru.md
  ru.docx
  ru.pdf
  ru.epub
  cover.ru.jpg
```

They may be produced by the local `pancratius downloads render` command using
pandoc, typst, templates, and committed fonts. That command can be friendly to a
non-developer workflow ("refresh downloads for changed works"), but the produced
files are durable content artifacts once committed.

For a multi-source work, source parts such as `ru-part1.docx`,
`ru-part2.docx`, and `ru-part3.docx` are provenance/source artifacts, not public
per-work downloads. The public route `/books/{slug}.docx` exists only when a
merged `ru.docx` or `en.docx` release artifact exists beside the Markdown.
Example: `/books/little-king.pdf` is one merged PDF; `/books/little-king.docx`
must also be one merged DOCX, not an arbitrary first part or a ZIP of parts.

The site build must not depend on pandoc or typst. If a work has no committed
`<lang>.pdf` or `<lang>.epub`, that route and link do not exist.

Generated `.md` downloads are not the same file as source Markdown. They strip
all frontmatter, rewrite image paths for the public URL space, and may add
explicit hard-break markers for verse content so ordinary Markdown readers
preserve poem and manifesto lineation. Source Markdown remains clean and
author-facing; public Markdown is content-only.

## Local Generation

The local/admin renderer (`pancratius downloads render`) owns document-rendering
choices:

- PDF renderer and templates.
- EPUB stylesheet and cover embedding.
- Font bundle.
- Image staging for body images.
- Version pinning and checksums when needed.

That belongs in the `pancratius` local tool and its asset docs, not in Astro
routes and not in CI. The site consumes the result.

## CI Contract

CI should:

- run content schema checks;
- build Astro;
- build Pagefind and sitemap;
- verify that every rendered download link points to an emitted file;
- publish `dist/`.

CI should not:

- install pandoc or typst;
- render PDFs or EPUBs;
- optimize DOCX;
- regenerate embeddings or graph data;
- maintain a `dist-cache` for document conversion.

## Bibliography sidecars in exports

Some source DOCX files contain long endmatter catalogs of the author's other
books with marketplace links. These are stored, when worth preserving, in the
work bundle's optional `bibliography.yaml`. They are not part of the website's
reader-facing recommendations.

Local PDF/EPUB generation may include `bibliography.yaml` as a collapsed/appendix-style
**author catalog** when the export format benefits from self-contained endmatter.
This is an export decision, not a page UI decision:

- website HTML: do not render catalog snapshots as "read next";
- EPUB/PDF: optionally append the catalog after the work and colophon;
- `.md` / `.txt`: keep the canonical body clean unless an explicit archival
  variant is added later.

LitRes or other external marketplace links belong in `bibliography.yaml`, not in
algorithmic recommendation data and not in `cross_refs` unless the author
explicitly cites that external URL in the body.

## Bulk archives at `/downloads/`

The production site ships **one** bulk archive: `all-md.zip` — every work as public Markdown, packaged with `<kind>/<lang>/<slug>.md` paths. Audience: LLM training, mirror sites, archival ingests. Markdown is the canonical text-first surface; PDF and EPUB are presentation renderings served per-work on each book's page.

The archive is not a raw copy of `src/content/**/*.md`. It contains the public export form only: frontmatter is removed, converter-only HTML wrappers are removed, inline HTML emphasis is converted to Markdown syntax where possible, and body image references become absolute Markdown image links.

Bulk PDF and EPUB archives are **not** shipped on the production host because they duplicate ~317 MB of bytes already served per-work, and the host has a 1 GB ceiling. They can still be built off-host via `node --experimental-strip-types build/bulk-archives.ts --formats=md,pdf,epub` for upload to GitHub Releases, the Internet Archive, or a Hugging Face dataset.

The `/downloads/` page is a short index with size and `sha256` for verification.

"Built" here means packaging already-committed artifacts. It does not mean rendering PDFs/EPUBs during CI. The prebuild archive step writes `.cache/bulk-archives/all-md.zip` plus `data/bulk-archives.json`; Astro then emits `/downloads/all-md.zip` through a static endpoint so local dev and production use the same URL.

Per-work artifacts never live under `/downloads/` — that's the bulk surface only.

## Out of scope here

Exact renderer flags, version pins, and font/template details belong in the
`pancratius` tool docs, not this architecture document.
