# Math font

Self-hosted font for MathML rendering (`src/styles/reading/math.css`). The site's
body/heading fonts come from `@fontsource-variable/*` packages via Astro's Fonts API;
this one has no package, so it is committed here directly.

- **`latinmodernmath.woff2`** — Latin Modern Math, the OpenType MATH font that carries
  every glyph our math uses, including the script capital (`\mathcal{C}` → 𝒞) and
  primes. From the Temml distribution (`temml.org/assets/latinmodernmath.woff2`),
  itself derived from GUST's Latin Modern. GUST Font License (the LaTeX Project Public
  License — free to use and redistribute).

Latin Modern Math has no Cyrillic, so `math.css` falls Cyrillic glyphs («Я», «К», …)
through to the body serif (`--serif`). The font downloads only on pages that actually
contain `<math>` — the `@font-face` is lazy.
