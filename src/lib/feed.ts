// RSS feed builder for the corpus.
//
// One builder serves the combined "latest" feed and the per-section videos /
// messages feeds. Items are the dated, episodic, consumable content a reader
// actually wants delivered — messages, videos, and poems — newest first. Books
// are deliberately absent: a feed item is an announcement, and books carry no
// real publication date (only a synthetic, number-derived one), so streaming
// "new books" would be dishonest. When books gain real dates an announcement
// adapter can join `collect()`.
//
// SOTA RSS 2.0: an `<atom:link rel="self">`, channel language/copyright,
// `<lastBuildDate>` pinned to the newest item (so the build stays
// deterministic — no wall-clock), Media RSS thumbnails on video items, and an
// XSL stylesheet so opening the feed in a browser is a readable subscribe page.

import rss from "@astrojs/rss";

import type { Locale } from "./locales";
import { originFor } from "./origins";
import { routedUrl } from "./i18n/routing";
import { FEED_COPY, feedUrl, type FeedSection } from "./feed-meta";
import { getAllMessagePairs, localizedMessagePairs, messageTags } from "./messages";
import { getAllVideoPairs, localizedVideoPairs, videoCoverAbsoluteUrl, videoTags } from "./videos";
import { getPairsByKind, localizedWorkPairs, poemDate, workTags } from "./works";

const MAX_ITEMS = 50;
const FEED_STYLESHEET = "/feed.xsl";
const ATOM_NS = "http://www.w3.org/2005/Atom";
const MEDIA_NS = "http://search.yahoo.com/mrss/";

const COPYRIGHT: Record<Locale, string> = {
  ru: "CC0 1.0 — общественное достояние.",
  en: "CC0 1.0 — public domain.",
};

interface FeedItem {
  title: string;
  description: string;
  /** Root-relative; `@astrojs/rss` resolves it against the channel `site`. */
  link: string;
  pubDate: Date;
  categories: string[];
  /** Raw per-item XML (e.g. `<media:thumbnail>`); already escaped. */
  customData?: string;
}

function escapeAttr(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}

async function messageItems(locale: Locale): Promise<FeedItem[]> {
  const pairs = await getAllMessagePairs();
  return localizedMessagePairs(pairs, locale).map(({ entry }) => ({
    title: entry.data.title,
    description: entry.data.description,
    link: routedUrl("message", entry.data.slug, locale),
    pubDate: new Date(entry.data.published_at),
    categories: [...messageTags(entry)],
  }));
}

async function videoItems(locale: Locale): Promise<FeedItem[]> {
  const pairs = await getAllVideoPairs();
  return localizedVideoPairs(pairs, locale).map(({ pair, entry }) => {
    const thumbnail = videoCoverAbsoluteUrl(pair, locale);
    return {
      title: entry.data.title,
      description: entry.data.description,
      link: routedUrl("video", entry.data.slug, locale),
      pubDate: new Date(entry.data.published_at),
      categories: [...videoTags(entry)],
      ...(thumbnail ? { customData: `<media:thumbnail url="${escapeAttr(thumbnail)}"/>` } : {}),
    };
  });
}

async function poemItems(locale: Locale): Promise<FeedItem[]> {
  const pairs = await getPairsByKind("poem");
  const items: FeedItem[] = [];
  for (const { entry } of localizedWorkPairs(pairs, locale)) {
    const date = poemDate(entry);
    if (!date) continue; // an undated poem has no place on a dated timeline
    items.push({
      title: entry.data.title,
      description: entry.data.description,
      link: routedUrl("poem", entry.data.slug, locale),
      pubDate: new Date(date),
      categories: [...workTags(entry)],
    });
  }
  return items;
}

async function collect(locale: Locale, section: FeedSection): Promise<FeedItem[]> {
  const items =
    section === "videos"
      ? await videoItems(locale)
      : section === "messages"
        ? await messageItems(locale)
        : (await Promise.all([messageItems(locale), videoItems(locale), poemItems(locale)])).flat();
  return items.sort((a, b) => b.pubDate.getTime() - a.pubDate.getTime());
}

/** Build the RSS response for one locale + section. */
export async function buildFeed(locale: Locale, section: FeedSection): Promise<Response> {
  const items = (await collect(locale, section)).slice(0, MAX_ITEMS);
  const copy = FEED_COPY[locale][section];
  const newest = items[0]?.pubDate;

  return rss({
    title: copy.title,
    description: copy.description,
    site: originFor(locale),
    stylesheet: FEED_STYLESHEET,
    xmlns: { atom: ATOM_NS, media: MEDIA_NS },
    items: items.map(item => ({
      title: item.title,
      description: item.description,
      link: item.link,
      pubDate: item.pubDate,
      categories: item.categories,
      ...(item.customData ? { customData: item.customData } : {}),
    })),
    customData:
      `<language>${locale}</language>` +
      `<copyright>${COPYRIGHT[locale]}</copyright>` +
      (newest ? `<lastBuildDate>${newest.toUTCString()}</lastBuildDate>` : "") +
      `<atom:link href="${escapeAttr(feedUrl(locale, section))}" rel="self" type="application/rss+xml"/>`,
  });
}
