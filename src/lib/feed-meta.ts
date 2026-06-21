// Feed identity and discovery — the pure half of the RSS surface.
//
// Channel copy, the feed path/URL, and the accessible label for the subscribe
// link. Pure TypeScript (no `astro:content`, no `@astrojs/rss`) so the chrome —
// FeedAutodiscovery, FeedLink, Footer — can import it without pulling the feed
// builder's content queries into their graph. The builder lives in `./feed.ts`.

import { LOCALE_META } from "./i18n/locale-meta";
import { originFor } from "./origins";
import type { Locale } from "./locales";

interface FeedCopy {
  title: string;
  description: string;
}

/** Channel title + description per locale. One combined "latest" feed. */
export const FEED_COPY: Record<Locale, FeedCopy> = {
  ru: {
    title: "Панкратиус — новое",
    description:
      "Новое в библиотеке Сергея Орехова (Панкратиуса): послания, видео и стихи. Свободно — людям и языковым моделям. CC0.",
  },
  en: {
    title: "Pancratius — latest",
    description:
      "New in Sergey Orekhov's (Pancratius) library: messages, videos, and poems. Free — for people and for language models. CC0.",
  },
};

/** Root-relative feed path, e.g. `/ru/feed.xml`. */
export function feedPath(locale: Locale): string {
  return `/${LOCALE_META[locale].urlPrefix}/feed.xml`;
}

/** Absolute feed URL on the locale's canonical origin (atom self-link, <head>). */
export function feedUrl(locale: Locale): string {
  return new URL(feedPath(locale), originFor(locale)).toString();
}

/** Accessible label for the visible subscribe link. */
export function feedAria(locale: Locale): string {
  return locale === "ru"
    ? `Подписаться на RSS-ленту «${FEED_COPY.ru.title}»`
    : `Subscribe to the RSS feed “${FEED_COPY.en.title}”`;
}
