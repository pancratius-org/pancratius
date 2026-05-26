// Pancratius book template for pandoc's --pdf-engine=typst.
//
// This file is a pandoc *template* (passed via --template), not a raw typst
// document. Pandoc substitutes its variable placeholders before typst sees
// the result. See docs/downloads.md and `pancratius downloads render` for how it
// is wired in.
//
// Fonts: bound to Source Serif 4 (body) and Inter (headings). Both are
// committed under pancratius/download_assets/fonts/ with their OFL licenses. typst
// is launched with --ignore-system-fonts so the build is reproducible across
// machines.

// ─────────────────────────────────────────────────────────────────────
// Pandoc emitter prelude.
//
// Pandoc's typst writer assumes a small set of helpers exist in the
// surrounding template (see `pandoc -D typst`). Since this file replaces
// pandoc's default template wholesale, we mirror the relevant pieces here.
// Keep them in sync with the pandoc version used by the local rendering script.
// ─────────────────────────────────────────────────────────────────────

#let horizontalrule = line(start: (25%,0%), end: (75%,0%))

#show terms.item: it => block(breakable: false)[
  #text(weight: "bold")[#it.term]
  #block(inset: (left: 1.5em, top: -0.4em))[#it.description]
]

#set table(inset: 6pt, stroke: none)

// ─────────────────────────────────────────────────────────────────────
// Document setup.
// ─────────────────────────────────────────────────────────────────────

// PDF metadata. `title` accepts string-or-content; `author` requires
// string-or-array-of-strings (typst 0.14). The author is fixed and
// quote-free; the title may contain typographic quotes, so we route it
// through content brackets so typst doesn't reparse the value as a string
// literal.
#set document(
  title: [$title$],
  author: "$author$",
)

#set page(
  paper: "a5",
  margin: (x: 1.8cm, y: 2cm),
  numbering: "1",
  number-align: center,
)

#set par(
  justify: true,
  leading: 0.72em,
  first-line-indent: 0pt,
)

#set text(
  font: "Source Serif 4",
  size: 10.5pt,
  lang: "$lang$",
  hyphenate: auto,
)

// Russian-aware smart quotes: typst's smartquote primitive picks the right
// glyphs per text.lang. Pandoc emits real Unicode quote marks for --smart
// inputs, so this mostly governs straight-quote fallback.
#set smartquote(enabled: true)

// Headings — editorial, not Word-default. Show rules keep all decoration
// (spacing, weight, size) inside the heading content so they nest safely if
// pandoc emits a heading inside a quote / list / figure container. Avoid
// `pagebreak()` inside show rules: typst forbids pagebreaks inside containers
// and pandoc routinely wraps quoted/captioned content in `quote(...)[]`.
#show heading: set text(font: "Inter")

#show heading.where(level: 1): it => block(above: 2.4em, below: 1.2em)[
  #set text(size: 20pt, weight: "semibold")
  #set par(first-line-indent: 0pt, justify: false)
  #it.body
]

#show heading.where(level: 2): it => block(above: 1.4em, below: 0.6em)[
  #set text(size: 14pt, weight: "semibold")
  #set par(first-line-indent: 0pt, justify: false)
  #it.body
]

#show heading.where(level: 3): it => block(above: 1em, below: 0.4em)[
  #set text(size: 11.5pt, weight: "semibold", style: "italic")
  #set par(first-line-indent: 0pt, justify: false)
  #it.body
]

// Block quotes — set in italic, slightly indented.
#show quote.where(block: true): it => {
  set text(style: "italic", size: 10pt)
  pad(x: 1.5em, y: 0.5em, it.body)
}

// Links — subtle ink, not screen-blue.
#show link: set text(fill: rgb("#3a3a3a"))

// Cover / title page.
$if(cover-path)$
#page(margin: 0pt, numbering: none)[
  #set align(center + horizon)
  #image("$cover-path$", height: 100%)
]
#page(numbering: none)[
  #set align(center + horizon)
  #v(1fr)
  #text(size: 22pt, weight: "semibold", font: "Inter")[$title$]
  #v(1.2em)
  #text(size: 12pt, font: "Inter")[$author$]
  #v(1fr)
  #text(size: 9pt, fill: rgb("#666666"), font: "Inter")[pancratius.ru]
]
$else$
#page(numbering: none)[
  #set align(center + horizon)
  #v(1fr)
  #text(size: 24pt, weight: "semibold", font: "Inter")[$title$]
  #v(1.6em)
  #text(size: 13pt, font: "Inter")[$author$]
  #v(1fr)
  #text(size: 9pt, fill: rgb("#666666"), font: "Inter")[pancratius.ru]
]
$endif$

// Body starts on a fresh page with the page counter reset to 1.
#counter(page).update(1)

// Body (pandoc-emitted typst).
$body$

// Colophon — CC0 footer on the last page.
#pagebreak(weak: true)
#v(1fr)
#align(center)[
  #set text(size: 9pt, fill: rgb("#666666"), font: "Inter")
  #line(length: 30%, stroke: 0.4pt + rgb("#999999"))
  #v(0.5em)
  $author$ · pancratius.ru \
  CC0 1.0 Universal — public domain \
  No rights reserved. Copy, translate, distribute, perform, build upon.
]
#v(1fr)
