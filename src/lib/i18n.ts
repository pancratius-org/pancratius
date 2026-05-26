// Locale config, URL shape, and language-pair routing.
//
// Every URL on the site comes through here. Route files compose URLs by
// calling these helpers — they never concatenate path strings by hand. This
// keeps the trailing-slash contract and the "/en/ prefix on non-default
// locale" rule in one place.

import { SEGMENT_OF, type CorpusWorkKind, type RoutedKind } from "./kinds";
import { LOCALES, DEFAULT_LOCALE, type Locale } from "./locales";

// Re-export kind/locale primitives so route code reaches for one module.
export type { CorpusWorkKind, RoutedKind };
export type { Locale };
export { LOCALES, DEFAULT_LOCALE };

// ─────────────────────────────────────────────────────────────────────
// Locale registry — the per-locale metadata table.
//
// The canonical locale *codes* and order, plus the default locale, live in
// `./locales` (a pure module the config and content-config can import too).
// This table hangs the per-locale metadata off that list: UI labels, long-form
// names, URL prefixes, Open Graph locale codes, the site display name, and the
// display fallback chain. Adding a third language is: add the code to `LOCALES`
// in `./locales`, add one entry here, author the per-locale strings in the
// various `Record<Locale, …>` copy dictionaries, and the route/SEO machinery
// follows. (`Record<Locale, LocaleMeta>` makes a missing entry a type error.)
// ─────────────────────────────────────────────────────────────────────

export interface LocaleMeta {
  /** Short label rendered in chrome (header nav, footer, switcher). */
  label:    string;
  /**
   * Long-form locale names, by the locale they are *displayed in*. Read as
   * `LOCALE_META[targetLocale].name[uiLocale]` for "the name of `target`,
   * written in `ui`'s language".
   */
  name:     Record<Locale, string>;
  /**
   * URL prefix segment (no slashes). The default locale's prefix is "". Which
   * locale is canonical is owned by `DEFAULT_LOCALE` in `./locales`, not by a
   * flag here — `localizePath` reads this prefix per locale.
   */
  urlPrefix: string;
  /** Open Graph `og:locale` code, e.g. "ru_RU". */
  ogLocale:  string;
  /** Display name of the site in this locale (EN never uses the Cyrillic spelling). */
  siteLabel: string;
  /** Locale to fall back to for derived display data when this one is absent. */
  fallback:  Locale;
}

export const LOCALE_META: Record<Locale, LocaleMeta> = {
  ru: {
    label:     "RU",
    name:      { ru: "Русский",     en: "Russian" },
    urlPrefix: "",
    ogLocale:  "ru_RU",
    siteLabel: "Панкратиус",
    fallback:  "ru",
  },
  en: {
    label:     "EN",
    name:      { ru: "Английский",  en: "English" },
    urlPrefix: "en",
    ogLocale:  "en_US",
    siteLabel: "Pancratius",
    fallback:  "ru",
  },
};

/** Prefix a root-relative path with the locale segment, except for the default locale. */
export function localizePath(path: string, locale: Locale): string {
  if (!path.startsWith("/")) {
    throw new Error(`localizePath expects an absolute path, got ${JSON.stringify(path)}`);
  }
  const prefix = LOCALE_META[locale].urlPrefix;
  if (!prefix) return path;
  return `/${prefix}${path}`;
}

/** Canonical routed-content URL for `(kind, slug)` in `locale`. Slug must be per-language. */
export function routedUrl(kind: RoutedKind, slug: string, locale: Locale): string {
  return localizePath(`/${SEGMENT_OF[kind]}/${slug}/`, locale);
}

/** Canonical corpus-work URL for `(kind, slug)` in `locale`. */
export function workUrl(kind: CorpusWorkKind, slug: string, locale: Locale): string {
  return routedUrl(kind, slug, locale);
}

/** Canonical kind-index URL in `locale` (e.g. `/books/` or `/en/poetry/`). */
export function kindIndexUrl(kind: RoutedKind, locale: Locale): string {
  return localizePath(`/${SEGMENT_OF[kind]}/`, locale);
}

/** Canonical static-page URL. */
export function pageUrl(slug: string, locale: Locale): string {
  return localizePath(`/${slug}/`, locale);
}

/** Canonical download endpoint URL for `(kind, slug, format)` in `locale`. */
export function downloadUrl(kind: CorpusWorkKind, slug: string, format: string, locale: Locale): string {
  // Endpoint URLs end in the file extension, not in `/`.
  const base = localizePath(`/${SEGMENT_OF[kind]}/`, locale);
  return `${base}${slug}.${format}`;
}

/** Home URL for `locale`. */
export function homeUrl(locale: Locale): string {
  const prefix = LOCALE_META[locale].urlPrefix;
  return prefix ? `/${prefix}/` : "/";
}

/** Names rendered in UI chrome (header nav, footer, switcher). Derived from the registry. */
export const LOCALE_LABEL: Record<Locale, string> = Object.fromEntries(
  LOCALES.map(loc => [loc, LOCALE_META[loc].label]),
) as Record<Locale, string>;

/**
 * Russian pluralization.
 *
 * Russian nouns take three forms when counted:
 *   - one  (1)              → 1 книга
 *   - few  (2–4)            → 2 книги
 *   - many (5–20)           → 5 книг
 *
 * The choice depends on the last two digits of the number:
 *   - 11–14 → many   (отличие от 1/2/3 — "одиннадцать книг")
 *   - mod 10 = 1 → one   (21 книга, 101 книга)
 *   - mod 10 = 2..4 → few (22 книги, 43 псалма)
 *   - else → many        (25 книг, 47 книг)
 *
 * The catalogue below holds the three forms for every count we render.
 * Add a new lemma when a new countable noun appears in copy.
 */
export type RuPluralForms = readonly [one: string, few: string, many: string];

export const RU_PLURALS = {
  book:           ["книга",         "книги",          "книг"]          as const,
  bookDative:     ["книге",         "книгам",         "книгам"]        as const,
  poem:           ["стихотворение", "стихотворения",  "стихотворений"] as const,
  psalm:          ["псалом",        "псалма",         "псалмов"]       as const,
  project:        ["проект",        "проекта",        "проектов"]      as const,
  direction:      ["направление",   "направления",    "направлений"]   as const,
  door:           ["дверь",         "двери",          "дверей"]        as const,
} as const;

/** Pick the right form of a Russian noun for the given count. */
export function plRu(n: number, forms: RuPluralForms): string {
  const abs = Math.abs(n);
  const mod10 = abs % 10;
  const mod100 = abs % 100;
  if (mod100 >= 11 && mod100 <= 14) return forms[2];
  if (mod10 === 1) return forms[0];
  if (mod10 >= 2 && mod10 <= 4) return forms[1];
  return forms[2];
}

/**
 * Long-form locale name for ARIA. Read as `LOCALE_NAME[uiLocale][targetLocale]`
 * — "the name of `target`, written in `ui`'s language". Derived from the
 * registry's per-target `name` records (which are keyed the other way round).
 */
export const LOCALE_NAME: Record<Locale, Record<Locale, string>> = Object.fromEntries(
  LOCALES.map(ui => [
    ui,
    Object.fromEntries(LOCALES.map(target => [target, LOCALE_META[target].name[ui]])),
  ]),
) as Record<Locale, Record<Locale, string>>;

export interface NavItem {
  /** Root-relative path that `localizePath` will prefix per locale. */
  path: string;
  label: Record<Locale, string>;
  /**
   * Slug of a `pages` collection entry backing this item. When set, the item
   * shows only in locales where authored content for that page exists.
   */
  pageSlug?: string;
}

/**
 * Header navigation. The same `path` is reused for every locale via
 * `localizePath`. Order follows design/_copy-v7.md.
 *
 * `/license/`, `/support/`, `/downloads/` live in the footer.
 */
export const HEADER_NAV: readonly NavItem[] = [
  { path: "/books/",          label: { ru: "Книги",          en: "Books" } },
  { path: "/poetry/",         label: { ru: "Поэзия",         en: "Poetry" } },
  { path: "/videos/",         label: { ru: "Видео",          en: "Video" } },
  { path: "/projects/",       label: { ru: "Проекты",        en: "Projects" } },
  { path: "/conceptosphere/", label: { ru: "Концептосфера",  en: "Concept map" } },
  { path: "/svetozar/",       label: { ru: "Светозар",       en: "Svetozar" }, pageSlug: "svetozar" },
  { path: "/about/",          label: { ru: "Человек",        en: "Human"    }, pageSlug: "about" },
  { path: "/mission/",        label: { ru: "Миссия",         en: "Mission"  }, pageSlug: "mission" },
  { path: "/search/",         label: { ru: "Поиск",          en: "Search" } },
] as const;

/** Footer-only utility links; same `pageSlug` rule applies. */
export interface FooterLink {
  path: string;
  label: Record<Locale, string>;
  pageSlug: string;
}

export const FOOTER_LINKS: readonly FooterLink[] = [
  { path: "/license/",   label: { ru: "Лицензия",     en: "License"      }, pageSlug: "license" },
  { path: "/support/",   label: { ru: "Поддержать",   en: "Support"      }, pageSlug: "support" },
  { path: "/downloads/", label: { ru: "Скачать всё",  en: "Download all" }, pageSlug: "downloads" },
] as const;

/**
 * Slugs reserved by structural routes — a page slug equal to one of these
 * would shadow an index URL. Used to validate the `pages` collection.
 */
export const RESERVED_PAGE_SLUGS = new Set([
  "books", "poetry", "projects", "videos", "conceptosphere",
  "search", "feed", "feed.xml", "robots.txt", "llms.txt",
  "en", "ru", "sitemap-index.xml",
]);
