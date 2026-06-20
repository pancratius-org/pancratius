import type { RoutedKind } from "../kinds";
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
  /**
   * Routed kind backing this item. When set, the item shows only in locales
   * where at least one entry of that kind is authored — so a RU-only section
   * (Послания today) does not advertise an empty EN shelf.
   */
  kindGate?: RoutedKind;
}

export const HEADER_NAV: readonly NavItem[] = [
  { path: "/books/", label: { ru: "Книги", en: "Books" } },
  { path: "/poetry/", label: { ru: "Поэзия", en: "Poetry" } },
  { path: "/messages/", label: { ru: "Послания", en: "Epistles" }, kindGate: "message" },
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
  { path: "/downloads/", label: { ru: "Скачать произведения", en: "Download works" }, pageSlug: "downloads" },
] as const;

export interface FooterExternalLink {
  href: string;
  label: Record<Locale, string>;
}

export const FOOTER_EXTERNAL_LINKS: readonly FooterExternalLink[] = [
  { href: "https://github.com/pancratius-org/pancratius", label: { ru: "GitHub", en: "GitHub" } },
  { href: "https://t.me/SPankratius", label: { ru: "Telegram", en: "Telegram" } },
  { href: "mailto:ask@pancratius.org", label: { ru: "ask@pancratius.org", en: "ask@pancratius.org" } },
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
  "messages",
  "conceptosphere",
  "search",
  "feed",
  "feed.xml",
  "robots.txt",
  "llms.txt",
  "en",
  "ru",
  "sitemap-ru.xml",
  "sitemap-org.xml",
]);
