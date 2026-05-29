import type { Locale } from "../locales";

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

export const HEADER_NAV: readonly NavItem[] = [
  { path: "/books/", label: { ru: "Книги", en: "Books" } },
  { path: "/poetry/", label: { ru: "Поэзия", en: "Poetry" } },
  { path: "/videos/", label: { ru: "Видео", en: "Video" } },
  { path: "/projects/", label: { ru: "Проекты", en: "Projects" } },
  { path: "/conceptosphere/", label: { ru: "Концептосфера", en: "Concept map" } },
  { path: "/svetozar/", label: { ru: "Светозар", en: "Svetozar" }, pageSlug: "svetozar" },
  { path: "/about/", label: { ru: "Человек", en: "Human" }, pageSlug: "about" },
  { path: "/mission/", label: { ru: "Миссия", en: "Mission" }, pageSlug: "mission" },
  { path: "/search/", label: { ru: "Поиск", en: "Search" } },
] as const;

export interface FooterLink {
  path: string;
  label: Record<Locale, string>;
  pageSlug: string;
}

export const FOOTER_LINKS: readonly FooterLink[] = [
  { path: "/license/", label: { ru: "Лицензия", en: "License" }, pageSlug: "license" },
  { path: "/support/", label: { ru: "Поддержать", en: "Support" }, pageSlug: "support" },
  { path: "/downloads/", label: { ru: "Скачать всё", en: "Download all" }, pageSlug: "downloads" },
] as const;

/**
 * Slugs reserved by structural routes — a page slug equal to one of these
 * would shadow an index URL. Used to validate the `pages` collection.
 */
export const RESERVED_PAGE_SLUGS = new Set([
  "books",
  "poetry",
  "projects",
  "videos",
  "conceptosphere",
  "search",
  "feed",
  "feed.xml",
  "robots.txt",
  "llms.txt",
  "en",
  "ru",
  "sitemap-index.xml",
]);
