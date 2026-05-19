# Pancratius i18n / Routing

The narrow contract for how language and URLs interact. Site-wide architecture is in [`architecture.md`](./architecture.md); the content shape is in [`content-model.md`](./content-model.md).

## First Principle

**One URL equals one resource.** Language, content, downloads, alternate-language links — all of it follows from the URL. No separate "UI language" and "content language" state. Two state spaces produce hybrid bugs ("why is the UI in RU but the page in EN?"); don't open the door.

## URL structure

- **Russian is default and has no prefix.** `/` is the Russian home; `/ru/` is not a separate route.
- **Other languages use a locale prefix.** English at `/en/`. Future locales at `/de/`, `/fr/`, etc.
- **Structural nouns stay English** (`/books/`, `/poetry/`, `/projects/`). Cyrillic in URLs percent-encodes badly on shares.
- **Slugs are ASCII-transliterated per language.** `01-evangelie-tsarstviya` (RU), `01-gospel-of-the-kingdom` (EN). Never Cyrillic in URLs.

### Per-language slug policy

The architecture supports slugs differing per language: each language file's
frontmatter `slug` drives its URL. But the policy is conservative — **localize
the URL only when the localized title is editorially real**.

- The folder key is the canonical RU ASCII work key (`content/books/01-evangelie-tsarstviya/`).
- `ru.md` `slug:` is always a Russian-derived ASCII transliteration.
- `en.md` `slug:` is the English-title ASCII transliteration **only when the EN title is settled**. Until then, reuse the RU slug.
- If the EN title is a fallback (AI-machine translation kept as placeholder, or no editorial pass), reuse the RU slug. `translation.source: ai` is the honest signal — EN work pages surface it as a "machine translation" line near the colophon with a link back to the RU original.

Why: a localized URL keyword is a small SEO signal; renaming an EN slug later
creates redirect debt that's worse than the missed signal. Page title, `<h1>`,
content, hreflang, description, and JSON-LD do the heavy lifting. Reuse is the
safe default; localize once the editorial pass lands.

```yaml
# ru.md
slug: 01-evangelie-tsarstviya
title: Евангелие Царствия

# en.md when the English title is editorially real
slug: 01-gospel-of-the-kingdom
title: Gospel of the Kingdom

# en.md when the title is still a fallback (RU slug + RU title; the
# colophon's "machine translation" line carries the honesty)
slug: 62-kniga-tishiny
title: Книга Тишины
translation:
  source: ai
```

`src/lib/i18n.ts` resolves URLs from the per-language `slug:` field; route
generation never derives the EN slug from the RU slug or from the folder name.
`data/slug-map.json` likewise records whatever each language file declares.

## Language switcher

Two buttons: `RU | EN`. Three or more locales will need a different control; defer until then.

- Clicks the alternate-language version of **the same work**.
- If no translation exists, the alternate button is visibly disabled: dimmed, `aria-disabled="true"`, `aria-label="Нет перевода"`, no `href`. Not a silent fallback to a section index — that violates the principle.

## Work pages vs locale indexes

These two surfaces follow different rules:

**Individual work pages** exist only when that work has authored content in that locale.
- `/books/{slug}/` exists for every book.
- `/en/books/{slug}/` exists only when an `en.md` exists for that book.
- Never render Russian body content under an `/en/...` work URL — that URL claims to be the English representation of the book; if it contains no book, it shouldn't exist.
- A request that lands on a missing `/en/books/{slug}/` resolves to the EN 404 / search page, which offers a link back to `/books/`.

**Locale indexes** may exist regardless of per-work coverage:
- `/en/books/` is an English-language index of the library. It can either show only EN-available books, or clearly separate them into "with English translation" and "Russian originals," linking the latter back to `/books/{slug}/`.
- Same shape for `/en/poetry/`, `/en/projects/`, `/en/search/`.

The language switcher on a work page reflects this rule: if the alternate doesn't exist, the button is visibly disabled (`aria-disabled="true"`, dimmed, no `href`), not a silent redirect to the index.

## SEO

For every page:

- `<html lang>` matches the page language.
- `<link rel="canonical">` points at the page itself.
- `<link rel="alternate" hreflang="...">` for every available translation, plus `x-default` pointing at the RU canonical.
- Localized Open Graph metadata (title, description). In v1, the work's `cover` field doubles as the OG image; per-work generated OG cards are a v2 upgrade.
- Book, poem, and project pages emit JSON-LD `CreativeWork` structured data with localized `name`, `description`, `url`, `image`, `inLanguage`, `author`, `license`, and `isPartOf`. Use `author.name: "Сергей Орехов"` and `author.alternateName: "Панкратиус"`. Keep this in a shared `src/lib/seo.ts` helper, not ad hoc per route.

### Sitemap pairing

`@astrojs/sitemap`'s built-in i18n alternate generation assumes parallel slugs across locales (`/books/foo/` ↔ `/en/books/foo/`). Pancratius's slugs differ per language. The sitemap therefore needs the integration's `i18n` block **plus** a custom `serialize` that attaches `links` per route using the `(kind, number)` pair resolver.

Implementation note: `astro.config.ts` cannot import from `astro:content`. Keep the alternates resolver pure (operate on a precomputed `data/slug-map.json` produced during the build pipeline), or generate a small build manifest just for sitemap consumption. Don't try to read content collections from inside the config.

## Page-language coverage

| Surface | Locale rule |
|---------|-------------|
| home, books index, poetry index, projects index, about, mission, svetozar, license, downloads, support, search, feed, conceptosphere | locale index/nav pages may exist in EN; individual work pages exist only when authored content for that locale exists |
| `/llms.txt`, `/robots.txt`, `/sitemap-index.xml` | root, language-agnostic |

The conceptosphere graph data is Russian (concept labels are RU lemmas; book
titles use whichever translation is available). The UI chrome — mode toggle,
search placeholder, side panel headings, mobile chip labels — is localised
like the rest of the site. An English-locale reader sees the same data
behind English chrome.
