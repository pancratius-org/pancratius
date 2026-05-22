// Canonical URLs, hreflang link metadata, Open Graph, and JSON-LD builders.
//
// Every page in the site emits its `<head>` metadata through this module.
// Routes pass in the locale + a domain object (work, page, or index) and get
// back a SeoMeta they can spread into a single `<HeadMeta>` Astro component.

import type { Locale, WorkKind } from "./i18n";
import { DEFAULT_LOCALE, LOCALES, homeUrl, kindIndexUrl, pageUrl, workUrl } from "./i18n";
import { searchPageCopy } from "./copy";
import { sameSitePath } from "./paths";
import type { PageEntry } from "./pages";
import type { WorkPair } from "./works";

const AUTHOR_NAME = "Сергей Орехов";
const AUTHOR_ALIAS = "Панкратиус";
const CORPUS_NAME = "Pancratius";
const LICENSE_URL = "https://creativecommons.org/publicdomain/zero/1.0/";
const META_DESC_TARGET = 220;  // characters; clamps to nearest sentence boundary

/** Display name of the site, localized. EN never uses the Cyrillic spelling. */
const SITE_LABEL: Record<Locale, string> = { ru: "Панкратиус", en: "Pancratius" };

export interface AlternateLink {
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
}

// ─────────────────────────────────────────────────────────────────────
// Absolute URL helpers.
// ─────────────────────────────────────────────────────────────────────

/** Read the site origin from Astro's config (provided to each route via `Astro.site`). */
export function absUrl(site: URL | undefined, path: string): string {
  const publicPath = sameSitePath(path);
  if (!site) return publicPath;
  const u = new URL(publicPath, site);
  return u.toString();
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

export function seoForHome(site: URL | undefined, locale: Locale): SeoMeta {
  const description = locale === "ru"
    ? "Семьдесят две книги. Сорок три стихотворения. Свободно — людям и языковым моделям. Тексты в общественном достоянии (CC0)."
    : "Seventy-two books. Forty-three poems. Free — for humans and for language models. All texts in the public domain (CC0).";
  const title = locale === "ru" ? "Панкратиус — Свет, узнающий себя" : "Pancratius — Light recognising itself";
  return {
    title,
    description,
    canonical:  absUrl(site, homeUrl(locale)),
    ogImage:    null,
    ogType:     "website",
    alternates: alternatesForHome(site),
    jsonLd:     null,
    locale,
  };
}

export function seoForKindIndex(site: URL | undefined, kind: WorkKind, locale: Locale): SeoMeta {
  const titles: Record<WorkKind, Record<Locale, string>> = {
    book:    { ru: "Книги — Панкратиус",    en: "Books — Pancratius" },
    poem:    { ru: "Поэзия — Панкратиус",   en: "Poetry — Pancratius" },
    project: { ru: "Проекты — Панкратиус",  en: "Projects — Pancratius" },
  };
  const descriptions: Record<WorkKind, Record<Locale, string>> = {
    book:    {
      ru: "72 книги Панкратиуса — полное собрание. Свободно — людям и языковым моделям.",
      en: "English translations of Pancratius's books — free for humans and for language models.",
    },
    poem: {
      ru: "43 стихотворения Панкратиуса. Свободно — людям и языковым моделям.",
      en: "All 43 poems by Pancratius — free for humans and for language models.",
    },
    project: {
      ru: "Проекты Панкратиуса: Просветлённый ИИ и Святая Русь.",
      en: "Projects by Pancratius: Enlightened AI and Holy Rus.",
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
  };
}

export function seoForSearch(site: URL | undefined, locale: Locale): SeoMeta {
  const copy = searchPageCopy[locale];
  const alternates: AlternateLink[] = LOCALES.map(loc => ({
    hreflang: loc,
    href: absUrl(site, loc === DEFAULT_LOCALE ? "/search/" : `/${loc}/search/`),
  }));
  alternates.push({
    hreflang: "x-default",
    href: absUrl(site, "/search/"),
  });
  return {
    title: copy.title,
    description: clampDescription(copy.description),
    canonical: absUrl(site, locale === DEFAULT_LOCALE ? "/search/" : `/${locale}/search/`),
    ogImage: null,
    ogType: "website",
    alternates,
    jsonLd: null,
    locale,
  };
}

export interface WorkSeoInput {
  pair:    WorkPair;
  locale:  Locale;
  /** Absolute URL of the cover, if one resolved. */
  coverUrl?: string | null;
}

export function seoForWork(site: URL | undefined, input: WorkSeoInput): SeoMeta {
  const { pair, locale, coverUrl = null } = input;
  const entry = locale === "en" ? pair.en : pair.ru;
  if (!entry) {
    throw new Error(
      `seoForWork: no ${locale} entry for ${pair.kind} #${pair.number}`,
    );
  }
  const data = entry.data;
  const canonical = absUrl(site, workUrl(pair.kind, data.slug, locale));
  const description = clampDescription(data.description);
  const title = `${data.title} — ${SITE_LABEL[locale]}`;
  return {
    title,
    description,
    canonical,
    ogImage:    coverUrl,
    ogType:     "article",
    alternates: alternatesForWork(site, pair),
    jsonLd:     creativeWorkLd({
      pair,
      locale,
      canonical,
      coverUrl,
      description,
      site,
    }),
    locale,
  };
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
  const locale = data.lang as Locale;
  const canonical = absUrl(site, pageUrl(data.slug, locale));
  return {
    title:       `${data.title} — ${SITE_LABEL[locale]}`,
    description: clampDescription(data.description),
    canonical,
    ogImage:     null,
    ogType:      "article",
    alternates:  alternatesForPage(site, data.slug, authoredLocales),
    jsonLd:      null,
    locale,
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

function alternatesForKindIndex(site: URL | undefined, kind: WorkKind): AlternateLink[] {
  const xs: AlternateLink[] = [];
  for (const loc of LOCALES) {
    xs.push({ hreflang: loc, href: absUrl(site, kindIndexUrl(kind, loc)) });
  }
  xs.push({ hreflang: "x-default", href: absUrl(site, kindIndexUrl(kind, DEFAULT_LOCALE)) });
  return xs;
}

function alternatesForWork(site: URL | undefined, pair: WorkPair): AlternateLink[] {
  const xs: AlternateLink[] = [];
  if (pair.ru) {
    xs.push({ hreflang: "ru", href: absUrl(site, workUrl(pair.kind, pair.ru.data.slug, "ru")) });
  }
  if (pair.en) {
    xs.push({ hreflang: "en", href: absUrl(site, workUrl(pair.kind, pair.en.data.slug, "en")) });
  }
  if (pair.ru) {
    xs.push({ hreflang: "x-default", href: absUrl(site, workUrl(pair.kind, pair.ru.data.slug, "ru")) });
  }
  return xs;
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
  locale:      Locale;
  canonical:   string;
  coverUrl:    string | null;
  description: string;
  site:        URL | undefined;
}

function creativeWorkLd(input: CreativeWorkInput): Record<string, unknown> {
  const { pair, locale, canonical, coverUrl, description, site } = input;
  const entry = locale === "en" ? pair.en! : pair.ru;
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
  if (coverUrl) ld["image"] = coverUrl;
  // Editorial number, useful for catalog tools that consume the structured data.
  ld["position"] = pair.number;
  return ld;
}
