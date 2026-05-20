// Locale config, URL shape, and language-pair routing.
//
// Every URL on the site comes through here. Route files compose URLs by
// calling these helpers — they never concatenate path strings by hand. This
// keeps the trailing-slash contract and the "/en/ prefix on non-default
// locale" rule in one place.

export type Locale = "ru" | "en";
export type WorkKind = "book" | "poem" | "project";

export const LOCALES: readonly Locale[] = ["ru", "en"] as const;
export const DEFAULT_LOCALE: Locale = "ru";

const SEGMENT: Record<WorkKind, string> = {
  book:    "books",
  poem:    "poetry",
  project: "projects",
};

/** Prefix a root-relative path with the locale segment, except for the default locale. */
export function localizePath(path: string, locale: Locale): string {
  if (!path.startsWith("/")) {
    throw new Error(`localizePath expects an absolute path, got ${JSON.stringify(path)}`);
  }
  if (locale === DEFAULT_LOCALE) return path;
  return `/${locale}${path}`;
}

/** Canonical work URL for `(kind, slug)` in `locale`. Slug must be the per-language slug. */
export function workUrl(kind: WorkKind, slug: string, locale: Locale): string {
  return localizePath(`/${SEGMENT[kind]}/${slug}/`, locale);
}

/** Canonical kind-index URL in `locale` (e.g. `/books/` or `/en/poetry/`). */
export function kindIndexUrl(kind: WorkKind, locale: Locale): string {
  return localizePath(`/${SEGMENT[kind]}/`, locale);
}

/** Canonical static-page URL. */
export function pageUrl(slug: string, locale: Locale): string {
  return localizePath(`/${slug}/`, locale);
}

/** Canonical download endpoint URL for `(kind, slug, format)` in `locale`. */
export function downloadUrl(kind: WorkKind, slug: string, format: string, locale: Locale): string {
  // Endpoint URLs end in the file extension, not in `/`.
  const base = localizePath(`/${SEGMENT[kind]}/`, locale);
  return `${base}${slug}.${format}`;
}

/** Home URL for `locale`. */
export function homeUrl(locale: Locale): string {
  return locale === DEFAULT_LOCALE ? "/" : `/${locale}/`;
}

/** Names rendered in UI chrome (header nav, footer, switcher). */
export const LOCALE_LABEL: Record<Locale, string> = {
  ru: "RU",
  en: "EN",
};

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

/** Long-form locale name for ARIA. Read as `LOCALE_NAME[uiLocale][targetLocale]`. */
export const LOCALE_NAME: Record<Locale, Record<Locale, string>> = {
  ru: { ru: "Русский", en: "Английский" },
  en: { ru: "Russian", en: "English"    },
};

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
  "books", "poetry", "projects", "conceptosphere",
  "search", "feed", "feed.xml", "robots.txt", "llms.txt",
  "en", "ru", "sitemap-index.xml",
]);
