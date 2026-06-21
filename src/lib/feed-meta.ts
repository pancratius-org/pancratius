// Feed identity and discovery — the pure half of the RSS surface.
//
// Channel copy, feed paths/URLs, and the autodiscovery links that go in every
// page <head>. Pure TypeScript (no `astro:content`, no `@astrojs/rss`) so the
// chrome — HeadMeta, Footer — can import it without pulling the feed builder's
// content queries into their graph. The builder itself lives in `./feed.ts`.

import { LOCALE_META } from "./i18n/locale-meta";
import { originFor } from "./origins";
import type { Locale } from "./locales";

/** The combined "latest" feed plus the two section feeds we publish. */
export type FeedSection = "all" | "videos" | "messages";

interface SectionCopy {
  title: string;
  description: string;
}

/** Channel title + description per locale and section. */
export const FEED_COPY: Record<Locale, Record<FeedSection, SectionCopy>> = {
  ru: {
    all: {
      title: "Панкратиус — новое",
      description:
        "Новое в библиотеке Сергея Орехова (Панкратиуса): послания, видео и стихи. Свободно — людям и языковым моделям. CC0.",
    },
    videos: {
      title: "Панкратиус — видео",
      description: "Новые видео Сергея Орехова (Панкратиуса). CC0.",
    },
    messages: {
      title: "Панкратиус — послания",
      description: "Новые послания Сергея Орехова (Панкратиуса). CC0.",
    },
  },
  en: {
    all: {
      title: "Pancratius — latest",
      description:
        "New in Sergey Orekhov's (Pancratius) library: messages, videos, and poems. Free — for people and for language models. CC0.",
    },
    videos: {
      title: "Pancratius — videos",
      description: "New videos from Sergey Orekhov (Pancratius). CC0.",
    },
    messages: {
      title: "Pancratius — messages",
      description: "New messages from Sergey Orekhov (Pancratius). CC0.",
    },
  },
};

/** Root-relative feed path, e.g. `/ru/feed.xml`, `/en/videos/feed.xml`. */
export function feedPath(locale: Locale, section: FeedSection): string {
  const prefix = LOCALE_META[locale].urlPrefix;
  const segment = section === "all" ? "" : `${section}/`;
  return `/${prefix}/${segment}feed.xml`;
}

/** Absolute feed URL on the locale's canonical origin (atom self-link, <head>). */
export function feedUrl(locale: Locale, section: FeedSection): string {
  return new URL(feedPath(locale, section), originFor(locale)).toString();
}

interface FeedLink {
  section: FeedSection;
  title: string;
  href: string;
}

/**
 * The feeds a page advertises in its `<head>`: always the combined feed for the
 * page's locale, plus the section feed when the reader is inside `/videos/` or
 * `/messages/`. Path-driven so no route has to thread feed props by hand.
 */
export function feedLinksFor(locale: Locale, pathname: string): FeedLink[] {
  const links: FeedLink[] = [
    { section: "all", title: FEED_COPY[locale].all.title, href: feedUrl(locale, "all") },
  ];
  const section = pathname.split("/")[2];
  if (section === "videos" || section === "messages") {
    links.push({ section, title: FEED_COPY[locale][section].title, href: feedUrl(locale, section) });
  }
  return links;
}
