# Pancratius Social Cards

Open Graph / Twitter images for shared links. The image renders **alongside** the
platform's own title + description (WhatsApp, Telegram, X, Facebook, … all draw those
from `og:title`/`og:description`), so the image is a **pure visual hook** — never a
card that reprints the title. Site-wide SEO/head rules: [`i18n-routing.md`](./i18n-routing.md).

## What each surface uses

- **Content** (book, poem, project, sub-page, video) → its **own cover / thumbnail**.
  Messaging apps show it large, respecting aspect, so a portrait cover reads well;
  wider feed cards crop to the centred subject. The cover already flows through Astro's
  asset pipeline; the route passes its absolute URL (on the resource locale's origin).
- **Cover-less surfaces** (home, section indexes, static pages, search, messages) → one
  committed **brand image**, `public/og/brand.jpg`. The platform's title chrome
  differentiates them, so they can share one image.
- **Conceptosphere** → a committed **still of its graph**, `public/og/conceptosphere.jpg`.

## Why no generated cards

The platform already prints the title; a second title baked into the image is
redundant noise — and Google's guidance is to avoid text in `og:image`. So the cover
(or the graph, or the brand mark) is the whole image. That also means **no build-time
generation**: covers come from the work bundles, and the brand + conceptosphere images
are two committed assets. `src/lib/og.ts` holds those two paths plus the
absolute-URL helper; `seo.ts` / `HeadMeta.astro` emit `og:image` + `og:image:alt` +
the Twitter `summary_large_image` set.

## Refreshing the committed images

- `public/og/brand.jpg` — the brand lockup (the real eclipse mark + wordmark, dark).
- `public/og/conceptosphere.jpg` — a still of the conceptosphere graph.

Replace either file in place; they are served as-is from `public/`.
