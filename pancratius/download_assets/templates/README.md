# Pancratius download templates and fonts

This directory holds the Typst PDF template and the EPUB CSS used by
`pancratius downloads render` when refreshing committed per-work release
artifacts. Sibling directory `../fonts/` holds the bundled fonts.

## Layout

```
pancratius/download_assets/templates/
  book.typ        — pandoc template for typst PDF output
  epub.css        — slim stylesheet for EPUB (used by --css)
  README.md       — this file

pancratius/download_assets/fonts/
  source-serif-4/ — body face (serif, full Cyrillic)
  inter/          — heading + UI face (sans, full Cyrillic)
```

Both font families are SIL Open Font License 1.1; each subdirectory ships
its own `OFL.txt`. Redistribution alongside the project is explicitly
permitted by the OFL.

## Fonts

- **Source Serif 4** 4.005R — the body face. Static styles only (Regular,
  Italic, Semibold, SemiboldIt, Bold, BoldIt). typst 0.14 does not yet
  support variable fonts and emits a warning if a variable TTF is loaded;
  we pin the statics to keep the build warning-free and metrics stable.
- **Inter** 4.1 — the heading/UI face. Static styles only (Regular,
  Italic, Medium, SemiBold, Bold, BoldItalic).

Source repos:
- https://github.com/adobe-fonts/source-serif (`4.005R` release)
- https://github.com/rsms/inter (`v4.1` release)

Both cover Cyrillic, Latin Extended, and modern punctuation. The
typographic continuity with the site is intentional — the web stack also
serves Source Serif 4 for body type.

## Tool install (macOS dev)

```
brew install pandoc typst poppler   # poppler is optional, for pdftoppm previews
```

Tested with:
- `pandoc 3.9.0.2`
- `typst 0.14.2`

CI does not install or run these tools. They are local/admin dependencies for
refreshing committed release artifacts.

## How the template is invoked

`pancratius downloads render` shells out roughly as follows:

```
pandoc <md> -o <pdf>
  --pdf-engine=typst
  --template pancratius/download_assets/templates/book.typ
  --pdf-engine-opt=--root=/
  --pdf-engine-opt=--ignore-system-fonts
  --pdf-engine-opt=--font-path=<abs>/downloads-fonts/source-serif-4
  --pdf-engine-opt=--font-path=<abs>/downloads-fonts/inter
  --metadata title=<title>
  --metadata lang=<ru|en>
  --metadata author=Сергей Орехов (Панкратиус)
  --metadata cover-path=<abs>/cover.<lang>.<ext>     # books only
```

- `--ignore-system-fonts` keeps output reproducible across machines.
- `--root=/` lets the template reference the cover image via absolute path
  (typst sandboxes file access to a configurable root; the default rejects
  paths outside the input file's directory).
- The cover-path metadata variable is consumed by the `$if(cover-path)$`
  branch in `book.typ`. Poems currently omit the cover and get a plain text title
  page; if a poem ever ships a cover image
  the same path will pick it up.

## Regeneration

Generated PDF/EPUB files are committed beside each work as
`src/content/<kind>/<work>/<lang>.{pdf,epub}`. The script is incremental by default:
it skips outputs newer than their source Markdown. Use `--force` when templates,
fonts, or renderer versions change and every artifact needs a refresh.

The Astro build only copies committed artifacts. It never runs Pandoc or Typst.
