// Canonical URLs, hreflang link metadata, Open Graph, and JSON-LD builders.
//
// Every page in the site emits its `<head>` metadata through this module.
// Routes pass in the locale + a domain object (work, page, or index) and get
// back a SeoMeta they can spread into a single `<HeadMeta>` Astro component.

import type { Locale, RoutedKind } from "./i18n";
import {
  DEFAULT_LOCALE,
  LOCALES,
  LOCALE_META,
  homeUrl,
  kindIndexUrl,
  localizePath,
  pageUrl,
  routedUrl,
  spellEnglishCardinal,
  spellRussianCardinal,
  workUrl,
} from "./i18n";
import { searchPageCopy } from "./i18n/copy";
import type { PageEntry } from "./pages";
import type { ProjectLanding, ProjectSubpage } from "./projects";
import {
  authoredWorkPairs,
  defaultWorkEntry,
  entryForAuthoredLocale,
  type WorkEntry,
  type WorkPair,
} from "./works";
import {
  authoredVideoPairs,
  baseEmbedUrlFor,
  defaultVideoEntry,
  entryForAuthoredVideoLocale,
  videoWatchLinks,
  type VideoPair,
} from "./videos";

const AUTHOR_NAME = "Сергей Орехов";
const AUTHOR_ALIAS = "Панкратиус";
const CORPUS_NAME = "Pancratius";
const LICENSE_URL = "https://creativecommons.org/publicdomain/zero/1.0/";
const META_DESC_TARGET = 220;  // characters; clamps to nearest sentence boundary

/** Display name of the site, localized. EN never uses the Cyrillic spelling. */
function siteLabel(locale: Locale): string {
  return LOCALE_META[locale].siteLabel;
}

interface AlternateLink {
  hreflang: string;  // "ru", "en", "x-default"
  href:     string;  // absolute URL
}

export interface SeoMeta {
  title:       string;
  description: string;
  canonical:   string;
  ogImage:    string | null;
  ogType:      "website" | "article";
  alternates:  AlternateLink[];
  jsonLd:      Record<string, unknown> | null;
  locale:      Locale;
  /** Open Graph `og:locale` code for this locale (e.g. "ru_RU"). */
  ogLocale:    string;
  /** Site display name for `og:site_name` in this locale. */
  siteName:    string;
}

// ─────────────────────────────────────────────────────────────────────
// Absolute URL helpers.
// ─────────────────────────────────────────────────────────────────────

/** Read the site origin from Astro's config (provided to each route via `Astro.site`). */
export function absUrl(site: URL | undefined, path: string): string {
  if (!site) return path;
  return new URL(path, site).toString();
}

// ─────────────────────────────────────────────────────────────────────
// Meta description clamping.
//
// The frontmatter `description` field can be 30 chars or 600. Bounds for an
// HTML <meta description> are roughly 150–300; we clamp to ~220 at a sentence
// boundary and fall back to a word boundary if the first sentence is itself
// too long. Punctuation respected: ., !, ?, …, RU/EN style.
// ─────────────────────────────────────────────────────────────────────

const SENTENCE_END_RE = /[.!?…](?:["»”')\]]+)?\s/g;

export function clampDescription(text: string, target = META_DESC_TARGET): string {
  const normalized = text.replace(/\s+/g, " ").trim();
  if (normalized.length <= target) return normalized;
  // Try to end on a sentence boundary at or before `target`.
  let lastBoundary = -1;
  let match: RegExpExecArray | null;
  SENTENCE_END_RE.lastIndex = 0;
  while ((match = SENTENCE_END_RE.exec(normalized)) !== null) {
    const end = match.index + match[0].length - 1;  // include punctuation, drop trailing space
    if (end > target) break;
    lastBoundary = end;
  }
  if (lastBoundary > target * 0.6) {
    return normalized.slice(0, lastBoundary).trim();
  }
  // Fall back to word boundary.
  const sliced = normalized.slice(0, target);
  const lastSpace = sliced.lastIndexOf(" ");
  const cut = lastSpace > target * 0.6 ? sliced.slice(0, lastSpace) : sliced;
  return cut.replace(/[\s,;:—–-]+$/u, "").trim() + "…";
}

// ─────────────────────────────────────────────────────────────────────
// Per-surface builders.
// ─────────────────────────────────────────────────────────────────────

const homeTitle: Record<Locale, string> = {
  ru: "Панкратиус — Свет, узнающий себя",
  en: "Pancratius — Light recognising itself",
};

/** Corpus tallies the build passes in so counts in meta never go stale. */
export interface CorpusCounts {
  books: number;
  poems: number;
}

// RU declensions used in the home meta sentence ("книги/книг", "стихотворения").
function ruBooksWord(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod100 >= 11 && mod100 <= 14) return "книг";
  if (mod10 === 1) return "книга";
  if (mod10 >= 2 && mod10 <= 4) return "книги";
  return "книг";
}
function ruPoemsWord(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod100 >= 11 && mod100 <= 14) return "стихотворений";
  if (mod10 === 1) return "стихотворение";
  if (mod10 >= 2 && mod10 <= 4) return "стихотворения";
  return "стихотворений";
}

/**
 * Home meta description, derived from live corpus tallies so the spelled-out
 * counts ("Семьдесят две книги. Сорок три стихотворения.") can never go stale.
 */
function homeDescription(locale: Locale, counts: CorpusCounts): string {
  if (locale === "en") {
    return `${spellEnglishCardinal(counts.books)} books. ${spellEnglishCardinal(counts.poems)} poems. Free — for humans and for language models. All texts in the public domain (CC0).`;
  }
  return `${spellRussianCardinal(counts.books, { feminine: true })} ${ruBooksWord(counts.books)}. ${spellRussianCardinal(counts.poems, { feminine: true })} ${ruPoemsWord(counts.poems)}. Свободно — людям и языковым моделям. Тексты в общественном достоянии (CC0).`;
}

export function seoForHome(site: URL | undefined, locale: Locale, counts: CorpusCounts): SeoMeta {
  return {
    title:       homeTitle[locale],
    description: homeDescription(locale, counts),
    canonical:  absUrl(site, homeUrl(locale)),
    ogImage:    null,
    ogType:     "website",
    alternates: alternatesForHome(site),
    jsonLd:     null,
    locale,
    ...ogMeta(locale),
  };
}

/** The registry-derived OG fields every SeoMeta carries. */
function ogMeta(locale: Locale): { ogLocale: string; siteName: string } {
  return { ogLocale: LOCALE_META[locale].ogLocale, siteName: LOCALE_META[locale].siteLabel };
}

export interface KindIndexCount {
  /** Total works of this kind in the library (the default-locale shelf size). */
  total: number;
}

export function seoForKindIndex(
  site: URL | undefined,
  kind: RoutedKind,
  locale: Locale,
  count?: KindIndexCount,
): SeoMeta {
  const titles: Record<RoutedKind, Record<Locale, string>> = {
    book:    { ru: "Книги — Панкратиус",    en: "Books — Pancratius" },
    poem:    { ru: "Поэзия — Панкратиус",   en: "Poetry — Pancratius" },
    project: { ru: "Проекты — Панкратиус",  en: "Projects — Pancratius" },
    video:   { ru: "Видео — Панкратиус",    en: "Video — Pancratius" },
  };
  // Counts are derived from the live corpus and threaded through here so the
  // numeric meta descriptions never go stale when a work is added or removed.
  const n = count?.total;
  const descriptions: Record<RoutedKind, Record<Locale, string>> = {
    book:    {
      ru: n != null
        ? `${n} ${ruBooksWord(n)} Панкратиуса — полное собрание. Свободно — людям и языковым моделям.`
        : "Книги Панкратиуса — полное собрание. Свободно — людям и языковым моделям.",
      en: "English translations of Pancratius's books — free for humans and for language models.",
    },
    poem: {
      ru: n != null
        ? `${n} ${ruPoemsWord(n)} Панкратиуса. Свободно — людям и языковым моделям.`
        : "Стихотворения Панкратиуса. Свободно — людям и языковым моделям.",
      en: n != null
        ? `All ${n} poems by Pancratius — free for humans and for language models.`
        : "All poems by Pancratius — free for humans and for language models.",
    },
    project: {
      ru: "Проекты Панкратиуса: Просветлённый ИИ и Святая Русь.",
      en: "Projects by Pancratius: Enlightened AI and Holy Rus.",
    },
    video: {
      ru: n != null
        ? `${n} видео Панкратиуса. Видеосерии и беседы — со страницей и письменным разбором, где он есть.`
        : "Видео Панкратиуса: беседы, видеосерии, разборы.",
      en: n != null
        ? `${n} catalogued videos by Pancratius. Talks and series — each on its own page, with written commentary where authored.`
        : "Catalogued videos by Pancratius: talks, series, commentary.",
    },
  };
  return {
    title:       titles[kind][locale],
    description: descriptions[kind][locale],
    canonical:   absUrl(site, kindIndexUrl(kind, locale)),
    ogImage:     null,
    ogType:      "website",
    alternates:  alternatesForKindIndex(site, kind),
    jsonLd:      null,
    locale,
    ...ogMeta(locale),
  };
}

export function seoForSearch(site: URL | undefined, locale: Locale): SeoMeta {
  const copy = searchPageCopy[locale];
  return {
    title: copy.title,
    description: clampDescription(copy.description),
    canonical: absUrl(site, localizePath("/search/", locale)),
    ogImage: null,
    ogType: "website",
    alternates: alternatesForSearch(site),
    jsonLd: null,
    locale,
    ...ogMeta(locale),
  };
}

function alternatesForSearch(site: URL | undefined): AlternateLink[] {
  const xs: AlternateLink[] = [];
  for (const loc of LOCALES) {
    xs.push({ hreflang: loc, href: absUrl(site, localizePath("/search/", loc)) });
  }
  xs.push({ hreflang: "x-default", href: absUrl(site, localizePath("/search/", DEFAULT_LOCALE)) });
  return xs;
}

export interface WorkSeoInput {
  pair:    WorkPair;
  locale:  Locale;
  /** Absolute URL of the cover, if one resolved. */
  coverUrl?: string;
}

export function seoForWork(site: URL | undefined, input: WorkSeoInput): SeoMeta {
  const { pair, locale, coverUrl = null } = input;
  // Existence: a work page in this locale only exists if the locale was
  // authored. Do NOT fall back — a missing entry here is a routing bug.
  const entry = entryForAuthoredLocale(pair, locale);
  if (!entry) {
    throw new Error(
      `seoForWork: no ${locale} entry for ${pair.kind} #${pair.number}`,
    );
  }
  const data = entry.data;
  const canonical = absUrl(site, workUrl(pair.kind, data.slug, locale));
  const description = clampDescription(data.description);
  const title = `${data.title} — ${siteLabel(locale)}`;
  return {
    title,
    description,
    canonical,
    ogImage:    coverUrl,
    ogType:     "article",
    alternates: alternatesForWork(site, pair),
    jsonLd:     creativeWorkLd({
      pair,
      entry,
      locale,
      canonical,
      coverUrl,
      description,
      site,
    }),
    locale,
    ...ogMeta(locale),
  };
}

export interface ProjectSeoInput {
  project: ProjectLanding;
  locale:  Locale;
  /** Absolute URL of the cover, if one resolved. */
  coverUrl?: string;
  /** Locales with an authored landing for this project — drives alternates. */
  authoredLocales: ReadonlySet<Locale>;
}

/**
 * SEO metadata for a project SECTION landing. Projects are original framing
 * (not a translation-of), so this does NOT go through `seoForWork`. Emits a
 * localized canonical, hreflang siblings for every authored landing locale, OG
 * `article`, and an `Article` JSON-LD scoped to the corpus series.
 */
export function seoForProject(site: URL | undefined, input: ProjectSeoInput): SeoMeta {
  const { project, locale, coverUrl = null, authoredLocales } = input;
  const data = project.data;
  const canonical = absUrl(site, routedUrl("project", data.slug, locale));
  const description = clampDescription(data.description);
  const title = `${data.title} — ${siteLabel(locale)}`;
  const ld: Record<string, unknown> = {
    "@context":    "https://schema.org",
    "@type":       "Article",
    "headline":    data.title,
    "description": description,
    "url":         canonical,
    "inLanguage":  locale,
    "author":      {
      "@type":         "Person",
      "name":          AUTHOR_NAME,
      "alternateName": AUTHOR_ALIAS,
    },
    "license":     LICENSE_URL,
    "isPartOf":    {
      "@type":  "CreativeWorkSeries",
      "name":   CORPUS_NAME,
      "url":    absUrl(site, homeUrl(DEFAULT_LOCALE)),
    },
  };
  if (coverUrl) ld.image = coverUrl;
  return {
    title,
    description,
    canonical,
    ogImage:     coverUrl,
    ogType:      "article",
    alternates:  alternatesForProject(site, data.slug, authoredLocales),
    jsonLd:      ld,
    locale,
    ...ogMeta(locale),
  };
}

export interface ProjectSubpageSeoInput {
  subpage: ProjectSubpage;
  locale:  Locale;
  /** Locales with an authored copy of this sub-page — drives alternates. */
  authoredLocales: ReadonlySet<Locale>;
}

/** SEO metadata for a project SUB-PAGE. Localized article inside the section. */
export function seoForProjectSubpage(
  site: URL | undefined,
  input: ProjectSubpageSeoInput,
): SeoMeta {
  const { subpage, locale, authoredLocales } = input;
  const data = subpage.data;
  const path = `/projects/${data.parent}/${data.slug}/`;
  const canonical = absUrl(site, localizePath(path, locale));
  const description = clampDescription(data.description);
  return {
    title:       `${data.title} — ${siteLabel(locale)}`,
    description,
    canonical,
    ogImage:     null,
    ogType:      "article",
    alternates:  alternatesForProjectSubpath(site, path, authoredLocales),
    jsonLd:      null,
    locale,
    ...ogMeta(locale),
  };
}

function alternatesForProjectSubpath(
  site: URL | undefined,
  path: string,
  authoredLocales: ReadonlySet<Locale>,
): AlternateLink[] {
  const xs: AlternateLink[] = [];
  let defaultHref: string | null = null;
  for (const loc of LOCALES) {
    if (authoredLocales.has(loc)) {
      const href = absUrl(site, localizePath(path, loc));
      xs.push({ hreflang: loc, href });
      if (loc === DEFAULT_LOCALE) defaultHref = href;
    }
  }
  if (defaultHref) {
    xs.push({ hreflang: "x-default", href: defaultHref });
  }
  return xs;
}

function alternatesForProject(
  site: URL | undefined,
  slug: string,
  authoredLocales: ReadonlySet<Locale>,
): AlternateLink[] {
  const xs: AlternateLink[] = [];
  let defaultHref: string | null = null;
  for (const loc of LOCALES) {
    if (authoredLocales.has(loc)) {
      const href = absUrl(site, routedUrl("project", slug, loc));
      xs.push({ hreflang: loc, href });
      if (loc === DEFAULT_LOCALE) defaultHref = href;
    }
  }
  if (defaultHref) {
    xs.push({ hreflang: "x-default", href: defaultHref });
  }
  return xs;
}

/**
 * SEO metadata for a static page. Pass the set of locales that have an
 * authored entry for this page so alternates list only real siblings.
 */
export function seoForPage(
  site: URL | undefined,
  page: PageEntry,
  authoredLocales: ReadonlySet<Locale>,
): SeoMeta {
  const data = page.data;
  const locale = data.lang;
  const canonical = absUrl(site, pageUrl(data.slug, locale));
  return {
    title:       `${data.title} — ${siteLabel(locale)}`,
    description: clampDescription(data.description),
    canonical,
    ogImage:     null,
    ogType:      "article",
    alternates:  alternatesForPage(site, data.slug, authoredLocales),
    jsonLd:      null,
    locale,
    ...ogMeta(locale),
  };
}

function alternatesForPage(
  site: URL | undefined,
  slug: string,
  authoredLocales: ReadonlySet<Locale>,
): AlternateLink[] {
  const xs: AlternateLink[] = [];
  let defaultHref: string | null = null;
  for (const loc of LOCALES) {
    if (authoredLocales.has(loc)) {
      const href = absUrl(site, pageUrl(slug, loc));
      xs.push({ hreflang: loc, href });
      if (loc === DEFAULT_LOCALE) defaultHref = href;
    }
  }
  if (defaultHref) {
    xs.push({ hreflang: "x-default", href: defaultHref });
  }
  return xs;
}

// ─────────────────────────────────────────────────────────────────────
// Alternates / hreflang.
//
// Per docs/i18n-routing.md every page lists every available translation plus
// x-default pointing at the RU canonical. Pages missing a translation simply
// omit the alternate — language switcher renders them as disabled.
// ─────────────────────────────────────────────────────────────────────

function alternatesForHome(site: URL | undefined): AlternateLink[] {
  const xs: AlternateLink[] = [];
  for (const loc of LOCALES) {
    xs.push({ hreflang: loc, href: absUrl(site, homeUrl(loc)) });
  }
  xs.push({ hreflang: "x-default", href: absUrl(site, homeUrl(DEFAULT_LOCALE)) });
  return xs;
}

function alternatesForKindIndex(site: URL | undefined, kind: RoutedKind): AlternateLink[] {
  const xs: AlternateLink[] = [];
  for (const loc of LOCALES) {
    xs.push({ hreflang: loc, href: absUrl(site, kindIndexUrl(kind, loc)) });
  }
  xs.push({ hreflang: "x-default", href: absUrl(site, kindIndexUrl(kind, DEFAULT_LOCALE)) });
  return xs;
}

function alternatesForWork(site: URL | undefined, pair: WorkPair): AlternateLink[] {
  // Existence: list one alternate per locale that was actually authored, so a
  // missing translation simply has no hreflang entry (switcher disables it).
  const xs: AlternateLink[] = [];
  for (const { entry, locale: loc } of authoredWorkPairs(pair)) {
    xs.push({ hreflang: loc, href: absUrl(site, workUrl(pair.kind, entry.data.slug, loc)) });
  }
  const canonical = defaultWorkEntry(pair);
  xs.push({
    hreflang: "x-default",
    href: absUrl(site, workUrl(pair.kind, canonical.data.slug, DEFAULT_LOCALE)),
  });
  return xs;
}

function alternatesForVideo(site: URL | undefined, pair: VideoPair): AlternateLink[] {
  const xs: AlternateLink[] = [];
  for (const { entry, locale: loc } of authoredVideoPairs(pair)) {
    xs.push({ hreflang: loc, href: absUrl(site, routedUrl("video", entry.data.slug, loc)) });
  }
  const canonical = defaultVideoEntry(pair);
  xs.push({
    hreflang: "x-default",
    href: absUrl(site, routedUrl("video", canonical.data.slug, DEFAULT_LOCALE)),
  });
  return xs;
}

export interface VideoSeoInput {
  pair:     VideoPair;
  locale:   Locale;
  coverUrl?: string | null;
}

/**
 * SEO metadata for a video page. Emits schema.org `VideoObject` JSON-LD
 * (thumbnailUrl, uploadDate, duration, contentUrl/embedUrl) so search engines
 * can surface the video as a rich result.
 */
export function seoForVideo(site: URL | undefined, input: VideoSeoInput): SeoMeta {
  const { pair, locale, coverUrl = null } = input;
  const entry = entryForAuthoredVideoLocale(pair, locale);
  if (!entry) {
    throw new Error(`seoForVideo: no ${locale} entry for video #${pair.number}`);
  }
  const data = entry.data;
  const canonical = absUrl(site, routedUrl("video", data.slug, locale));
  const description = clampDescription(data.description);
  const watchLinks = videoWatchLinks(entry);
  const embedUrl = baseEmbedUrlFor(entry);
  const ld: Record<string, unknown> = {
    "@context":     "https://schema.org",
    "@type":        "VideoObject",
    "name":         data.title,
    "description":  description,
    "uploadDate":   data.published_at,
    "duration":     data.duration,
    "url":          canonical,
    "inLanguage":   locale,
    "author":       {
      "@type":         "Person",
      "name":          AUTHOR_NAME,
      "alternateName": AUTHOR_ALIAS,
    },
    "license":      LICENSE_URL,
    "isPartOf":     {
      "@type": "CreativeWorkSeries",
      "name":  CORPUS_NAME,
      "url":   absUrl(site, homeUrl(DEFAULT_LOCALE)),
    },
  };
  if (coverUrl) ld.thumbnailUrl = coverUrl;
  ld.contentUrl = watchLinks.primary.url;
  if (embedUrl !== null) ld.embedUrl = embedUrl;
  return {
    title:       `${data.title} — ${siteLabel(locale)}`,
    description,
    canonical,
    ogImage:     coverUrl,
    ogType:      "article",
    alternates:  alternatesForVideo(site, pair),
    jsonLd:      ld,
    locale,
    ...ogMeta(locale),
  };
}

export function switcherAlternatesFromSeo(seo: SeoMeta): Partial<Record<Locale, string>> {
  const alternates: Partial<Record<Locale, string>> = {};
  for (const alt of seo.alternates) {
    if (!LOCALES.includes(alt.hreflang as Locale)) continue;
    alternates[alt.hreflang as Locale] = sameOriginPath(alt.href);
  }
  return alternates;
}

function sameOriginPath(href: string): string {
  if (!/^[a-z][a-z0-9+.-]*:/i.test(href)) return href;
  try {
    const url = new URL(href);
    return `${url.pathname}${url.search}${url.hash}`;
  } catch {
    return href;
  }
}

// ─────────────────────────────────────────────────────────────────────
// JSON-LD CreativeWork.
// ─────────────────────────────────────────────────────────────────────

interface CreativeWorkInput {
  pair:        WorkPair;
  entry:       WorkEntry;
  locale:      Locale;
  canonical:   string;
  coverUrl:    string | null;
  description: string;
  site:        URL | undefined;
}

function creativeWorkLd(input: CreativeWorkInput): Record<string, unknown> {
  const { pair, entry, locale, canonical, coverUrl, description, site } = input;
  const ld: Record<string, unknown> = {
    "@context":   "https://schema.org",
    "@type":      "CreativeWork",
    "name":       entry.data.title,
    "description": description,
    "url":        canonical,
    "inLanguage": locale,
    "author":     {
      "@type":         "Person",
      "name":          AUTHOR_NAME,
      "alternateName": AUTHOR_ALIAS,
    },
    "license":    LICENSE_URL,
    "isPartOf":   {
      "@type":  "CreativeWorkSeries",
      "name":   CORPUS_NAME,
      "url":    absUrl(site, homeUrl(DEFAULT_LOCALE)),
    },
  };
  if (coverUrl) ld.image = coverUrl;
  // Editorial number, useful for catalog tools that consume the structured data.
  ld.position = pair.number;
  return ld;
}
