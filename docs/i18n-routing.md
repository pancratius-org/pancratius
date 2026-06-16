# Pancratius i18n / Routing

Language and URL rules. Site-wide architecture is in
[`architecture.md`](./architecture.md); content shape is in
[`content-model.md`](./content-model.md).

## Rule

One URL equals one resource. Language, content, downloads, and alternate-language
links all follow from the URL. There is no separate UI-language state.

Reason: two language states create hybrid bugs. A page with EN chrome and a RU
body is not a fallback; it is the wrong resource at that URL.

## URL structure

- **Every locale is prefixed.** Russian at `/ru/`, English at `/en/`, future locales at `/de/`, `/fr/`, etc. There is no unprefixed content tree.
- **The apex `/` is a host-decided redirect.** The build bakes `/ → /ru/` (the default locale) as a meta-refresh; each host upgrades it to a true 301 (`.ru/ → /ru/`, `.org/ → /en/`). The apex serves no content.
- **Structural nouns stay English** (`/ru/books/`, `/en/poetry/`). Cyrillic in URLs percent-encodes badly on shares.
- **Slugs are ASCII-transliterated per language.** `01-evangelie-tsarstviya` (RU), `01-gospel-of-the-kingdom` (EN). Never Cyrillic in URLs.

### Per-language slug policy

The architecture supports slugs differing per language: each language file's
frontmatter `slug` drives its URL. But the policy is conservative — **localize
the URL only when the localized title is editorially real**.

- The folder key is the canonical RU ASCII work key (`src/content/books/01-evangelie-tsarstviya/`).
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

`src/lib/i18n/` resolves URLs from the per-language `slug:` field; route
generation never derives the EN slug from the RU slug or from the folder name.
`data/slug-map.json` likewise records whatever each language file declares.

## Language switcher

Two buttons: `RU | EN`. Three or more locales will need a different control; defer until then.

- Clicks the alternate-language version of **the same work**.
- If no translation exists, the alternate button is visibly disabled: dimmed, `aria-disabled="true"`, `aria-label="Нет перевода"`, no `href`. Not a silent fallback to a section index — that violates the principle.

## Work pages vs locale indexes

These two surfaces follow different rules:

**Individual work pages** exist only when that work has authored content in that locale.
- `/ru/books/{slug}/` exists for every book.
- `/en/books/{slug}/` exists only when an `en.md` exists for that book.
- Never render Russian body content under an `/en/...` work URL — that URL claims to be the English representation of the book; if it contains no book, it shouldn't exist.
- A request that lands on a missing `/en/books/{slug}/` resolves to the EN 404 / search page, which offers a link back to `/en/books/`.

**Locale indexes** may exist regardless of per-work coverage:
- `/en/books/` is an English-language index of the library. It can either show only EN-available books, or clearly separate them into "with English translation" and "Russian originals," linking the latter back to `/ru/books/{slug}/`.
- Same shape for `/en/poetry/`, `/en/projects/`, `/en/search/`.

The language switcher on a work page reflects this rule: if the alternate doesn't exist, the button is visibly disabled (`aria-disabled="true"`, dimmed, no `href`), not a silent redirect to the index.

## SEO

For every page:

- `<html lang>` matches the page language.
- `<link rel="canonical">` points at the page itself, on its locale's canonical origin. Origin is a function of the resource's **locale** (`src/lib/origins.ts`): RU → `pancratius.ru`, EN → `pancratius.org`, a new locale → the global `.org`. Independent of which mirror served the bytes.
- `<link rel="alternate" hreflang="...">` for every authored translation, cross-origin (RU → `.ru`, EN → `.org`), plus `x-default` → the **EN** version when English is authored (the global face), else the default-locale (RU) version.
- The canonical and hreflang links carry no extra attributes on the canonical, and the same-origin language switcher (`/ru/x ↔ /en/x`) is a separate, human-facing axis from the cross-origin hreflang map.
- Localized Open Graph metadata (title, description). In v1, the work's `cover` field doubles as the OG image; per-work generated OG cards are a v2 upgrade.
- Book, poem, and project pages emit JSON-LD `CreativeWork` structured data with localized `name`, `description`, `url`, `image`, `inLanguage`, `author`, `license`, and `isPartOf`. Use `author.name: "Сергей Орехов"` and `author.alternateName: "Панкратиус"`. Keep this in a shared `src/lib/seo.ts` helper, not ad hoc per route.

### Sitemaps

Each origin gets its own sitemap, because a sitemap may only list URLs on its own host: `sitemap-ru.xml` (`pancratius.ru`) and `sitemap-org.xml` (`pancratius.org`). Each lists its locale's canonical URLs and carries reciprocal cross-origin `<xhtml:link>` hreflang alternates plus `x-default`. Both files ship to both mirrors (absolute URLs stay valid wherever the file sits); the shared `robots.txt` lists both.

Slugs differ per language, so work/page alternates come from the precomputed `data/slug-map.json` (built by `build/slug-map.ts`); shared-path surfaces (indexes, home, project sub-pages) resolve alternates by swapping the locale prefix. The emitter is a build step (`build/sitemap.ts`) — `astro.config.ts` cannot read content collections, and the per-origin split fights `@astrojs/sitemap`'s single-`site` model.

## Page-language coverage

| Surface | Locale rule |
|---------|-------------|
| home, books index, poetry index, projects index, about, mission, svetozar, license, downloads, support, search, feed, conceptosphere | locale index/nav pages may exist in EN; individual work pages exist only when authored content for that locale exists |
| `/llms.txt`, `/robots.txt`, `/sitemap-ru.xml`, `/sitemap-org.xml` | root, language-agnostic |

The conceptosphere graph data is Russian (concept labels are RU lemmas; book
titles use whichever translation is available). The UI chrome — mode toggle,
search placeholder, side panel headings, mobile chip labels — is localised
like the rest of the site. An English-locale reader sees the same data
behind English chrome.
