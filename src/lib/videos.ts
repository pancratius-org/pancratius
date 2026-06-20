// Video discovery, language pairing, channels, and layout decision.
//
// Videos are a routed kind but NOT a corpus work — they live outside the
// `WorkPair` machinery in their own `VideoPair` type. The pairing rule is the
// same shape (`(kind, number)`), but videos carry no download matrix and no
// converter, so collapsing them into `works.ts` would import non-work
// concerns into the work-pair API. They stay separate by design.
//
// Route files import from here; they never touch `astro:content` directly so
// the (kind, number) invariant and cover-resolution policy stay in one place.

import { getCollection, type CollectionEntry } from "astro:content";

import { DEFAULT_LOCALE, LOCALE_META, LOCALES, type Locale } from "./i18n";
import { parseCoverPath, type CoverRef } from "./cover-path";
import { originFor } from "./origins";
import { localizedEmbedUrl } from "./video-format";

// Re-export the pure formatter so callers `import { formatDuration } from "@/lib/videos"`.
export { formatDuration } from "./video-format";

export type VideoEntry = CollectionEntry<"videos">;
export type VideoChannel = CollectionEntry<"videoChannels">;
type VideoSource = VideoEntry["data"]["sources"][number];
type DefaultVideoLocale = typeof DEFAULT_LOCALE;
type TranslatedVideoLocale = Exclude<Locale, DefaultVideoLocale>;
type VideoTranslations = Partial<Record<TranslatedVideoLocale, VideoEntry>>;
export type VideoPlatform = VideoSource["platform"];

/**
 * A video and its translations, keyed by `(kind, number)`. The default-locale
 * entry is guaranteed to exist (enforced in `getAllVideoPairs`); translated
 * entries are present only when that locale's `.md` file exists.
 */
export interface VideoPair {
  number: number;
  /** Canonical default-locale entry. Every pair has one. */
  defaultEntry: VideoEntry;
  /** Authored non-default-locale entries keyed by locale. */
  translations: VideoTranslations;
}

export interface LocalizedVideoPair {
  pair:   VideoPair;
  entry:  VideoEntry;
  locale: Locale;
}

export interface DisplayVideoEntry {
  entry:      VideoEntry;
  linkLocale: Locale;
}

interface VideoWatchLink {
  platform: VideoPlatform;
  url:      string;
}

export interface VideoWatchLinks {
  primary: VideoWatchLink;
  mirrors: VideoWatchLink[];
}

let _pairsCache: VideoPair[] | null = null;
let _channelsCache: VideoChannel[] | null = null;

/** The canonical default-locale entry. Every `VideoPair` has one. */
export function defaultVideoEntry(pair: VideoPair): VideoEntry {
  return pair.defaultEntry;
}

function videoBundleKey(pair: VideoPair): string {
  const id = defaultVideoEntry(pair).id;
  const separator = id.indexOf("--");
  if (separator === -1) throw new Error(`video entry id ${JSON.stringify(id)} is missing its locale separator`);
  return id.slice(0, separator);
}

/**
 * All videos in the corpus, paired across languages by `number`.
 * Throws if any video lacks a default-locale entry — the default-locale file
 * is the canonical anchor.
 *
 * Sort order: most recently published first (`published_at` desc, then
 * `number` desc as tiebreaker). The grid leads with new uploads, which is
 * what a video catalogue is for.
 */
export async function getAllVideoPairs(): Promise<VideoPair[]> {
  if (_pairsCache !== null) return _pairsCache;

  const all = await getCollection("videos");
  const pairs = videoPairsFromBuckets(videoBuckets(all));

  _pairsCache = pairs;
  return _pairsCache;
}

function videoBuckets(all: readonly VideoEntry[]): Map<number, Partial<Record<Locale, VideoEntry>>> {
  const buckets = new Map<number, Partial<Record<Locale, VideoEntry>>>();
  for (const entry of all) {
    const bucket = buckets.get(entry.data.number) ?? {};
    // Two entries for the same `(number, lang)` would silently overwrite
    // each other and lose data — surface it as an integrity error instead.
    const lang = entry.data.lang;
    const existing = bucket[lang];
    if (existing) {
      throw new Error(
        `Duplicate video #${entry.data.number}/${lang}: ${existing.id} and ${entry.id} share an (number, lang)`,
      );
    }
    bucket[lang] = entry;
    buckets.set(entry.data.number, bucket);
  }
  return buckets;
}

function videoPairsFromBuckets(buckets: ReadonlyMap<number, Partial<Record<Locale, VideoEntry>>>): VideoPair[] {
  return [...buckets.entries()]
    .map(([number, entries]) => videoPairFromBucket(number, entries))
    .sort(compareVideoPairs);
}

function videoPairFromBucket(number: number, entries: Partial<Record<Locale, VideoEntry>>): VideoPair {
  const canonical = entries[DEFAULT_LOCALE];
  if (!canonical) {
    throw new Error(
      `Video #${number} has translations but no ${DEFAULT_LOCALE} canonical entry`,
    );
  }
  const translations: VideoTranslations = {};
  for (const locale of LOCALES) {
    if (locale === DEFAULT_LOCALE) continue;
    const entry = entries[locale];
    if (entry !== undefined) translations[locale] = entry;
  }
  return { number, defaultEntry: canonical, translations };
}

function compareVideoPairs(a: VideoPair, b: VideoPair): number {
  const ad = defaultVideoEntry(a).data.published_at;
  const bd = defaultVideoEntry(b).data.published_at;
  if (ad !== bd) return ad < bd ? 1 : -1;
  return b.number - a.number;
}

/**
 * Authored-locale selector for route existence. Returns null when this pair
 * has no authored file in `locale`; it deliberately does not fall back.
 */
export function entryForAuthoredVideoLocale(pair: VideoPair, locale: Locale): VideoEntry | null {
  if (locale === DEFAULT_LOCALE) return pair.defaultEntry;
  return pair.translations[locale] ?? null;
}

function localizedVideoPair(pair: VideoPair, locale: Locale): LocalizedVideoPair | null {
  const entry = entryForAuthoredVideoLocale(pair, locale);
  if (entry === null) return null;
  return { pair, entry, locale };
}

export function localizedVideoPairs(
  pairs: readonly VideoPair[],
  locale: Locale,
): LocalizedVideoPair[] {
  const localized: LocalizedVideoPair[] = [];
  for (const pair of pairs) {
    const item = localizedVideoPair(pair, locale);
    if (item) localized.push(item);
  }
  return localized;
}

export function authoredVideoPairs(pair: VideoPair): LocalizedVideoPair[] {
  const authored: LocalizedVideoPair[] = [];
  for (const locale of LOCALES) {
    const item = localizedVideoPair(pair, locale);
    if (item !== null) authored.push(item);
  }
  return authored;
}

/**
 * Display selector for cards/CTAs that may fall back through the locale
 * registry. `linkLocale` tells the caller which real route owns the entry.
 *
 * USE FOR DISPLAY ONLY — routes must use `entryForAuthoredVideoLocale` or
 * `localizedVideoPairs` to decide existence.
 */
export function displayVideoEntry(pair: VideoPair, requestedLocale: Locale): DisplayVideoEntry {
  const seen = new Set<Locale>();
  let current: Locale = requestedLocale;
  while (!seen.has(current)) {
    const entry = entryForAuthoredVideoLocale(pair, current);
    if (entry !== null) return { entry, linkLocale: current };
    seen.add(current);
    const next = LOCALE_META[current].fallback;
    if (next === current) break;
    current = next;
  }
  return { entry: defaultVideoEntry(pair), linkLocale: DEFAULT_LOCALE };
}

// ─────────────────────────────────────────────────────────────────────
// Channels.
// ─────────────────────────────────────────────────────────────────────

// Display order for the /videos/ channel strip: the main channel leads, the
// rest follow by id. `getCollection` returns channels sorted by entry id, which
// would otherwise float "arabic" above "main".
const CHANNEL_ORDER = ["main"] as const;
const CHANNEL_RANK = new Map<string, number>(
  CHANNEL_ORDER.map((id, rank) => [id, rank]),
);

function channelRank(channel: VideoChannel): number {
  return CHANNEL_RANK.get(channel.id) ?? Number.POSITIVE_INFINITY;
}

function compareChannels(a: VideoChannel, b: VideoChannel): number {
  const rankDelta = channelRank(a) - channelRank(b);
  return rankDelta !== 0 ? rankDelta : a.id.localeCompare(b.id);
}

export async function getChannels(): Promise<VideoChannel[]> {
  if (_channelsCache !== null) return _channelsCache;
  const channels = await getCollection("videoChannels");
  _channelsCache = [...channels].sort(compareChannels);
  return _channelsCache;
}

async function findChannel(key: string): Promise<VideoChannel | null> {
  const all = await getChannels();
  return all.find(c => c.id === key) ?? null;
}

/** The channel a video's primary source attributes itself to, if any. */
export async function channelForEntry(entry: VideoEntry): Promise<VideoChannel | null> {
  const channel = primaryVideoSource(entry).channel;
  return channel === undefined ? null : findChannel(channel);
}

// ─────────────────────────────────────────────────────────────────────
// Cover resolution. The naming policy lives in `cover-path.ts` (shared with
// works); here we only resolve which entry's cover to use and glob the video
// content root for the built asset URL. Videos don't permit SVG covers.
// ─────────────────────────────────────────────────────────────────────

const VIDEO_COVER_URLS = import.meta.glob<string>(
  "/src/content/videos/**/cover.*.{jpg,jpeg,png,webp,avif}",
  { eager: true, query: "?url", import: "default" },
);

function resolveVideoCover(pair: VideoPair, locale: Locale): CoverRef | null {
  const localized = entryForAuthoredVideoLocale(pair, locale);
  if (localized !== null) {
    const ref = parseCoverPath(localized.data.cover, { context: "Video cover path" });
    if (ref) return ref;
  }
  if (locale !== DEFAULT_LOCALE) {
    const fallback = defaultVideoEntry(pair);
    const ref = parseCoverPath(fallback.data.cover, { context: "Video cover path" });
    if (ref) return ref;
  }
  return null;
}

export function videoCoverAssetUrl(pair: VideoPair, locale: Locale): string | null {
  const cover = resolveVideoCover(pair, locale);
  if (!cover) return null;
  const key = `/src/content/videos/${videoBundleKey(pair)}/${cover.rel.replace(/^\.\//, "")}`;
  return VIDEO_COVER_URLS[key] ?? null;
}

export function videoCoverAbsoluteUrl(pair: VideoPair, locale: Locale): string | null {
  const rel = videoCoverAssetUrl(pair, locale);
  if (rel === null) return null;
  return new URL(rel, originFor(locale)).toString();
}

// ─────────────────────────────────────────────────────────────────────
// Embed URL helpers.
// ─────────────────────────────────────────────────────────────────────

/**
 * Primary embed URL for a video, localized to `locale`. Prefers explicit
 * `embed_url`, else derives the nocookie URL. The player UI follows `locale`
 * (`hl`); for a non-default locale we also prefer that caption track and force
 * captions on (`cc_lang_pref` + `cc_load_policy`), since the audio stays
 * Russian — so the EN page of a RU video shows English UI and English subtitles.
 */
export function embedUrlFor(entry: VideoEntry, locale: Locale): string | null {
  const base = baseEmbedUrlFor(entry);
  if (base === null) return null;
  return localizedEmbedUrl(base, locale, locale === DEFAULT_LOCALE ? null : locale);
}

/** Source-platform watch URL for a video's primary source. */
export function watchUrlFor(entry: VideoEntry): string {
  return primaryVideoSource(entry).url;
}

/** Primary watch link plus mirror links, stripped to rendering-safe fields. */
export function videoWatchLinks(entry: VideoEntry): VideoWatchLinks {
  const { primary, mirrors } = videoSources(entry);
  return {
    primary: sourceWatchLink(primary),
    mirrors: mirrors.map(sourceWatchLink),
  };
}

/** Embed URL without player-localization params, for metadata and feeds. */
export function baseEmbedUrlFor(entry: VideoEntry): string | null {
  return embedBaseUrl(entry, primaryVideoSource(entry));
}

/** Flatten playlist titles into the video's `tags` list (de-duplicated). */
export function videoTags(entry: VideoEntry): readonly string[] {
  const tags = new Set(entry.data.tags);
  for (const playlist of entry.data.playlists ?? []) {
    tags.add(playlist.title);
  }
  return [...tags];
}

function primaryVideoSource(entry: VideoEntry): VideoSource {
  return videoSources(entry).primary;
}

function videoSources(entry: VideoEntry): { primary: VideoSource; mirrors: VideoSource[] } {
  const [primary, ...mirrors] = entry.data.sources;
  if (primary === undefined) {
    throw new Error(`Video entry ${entry.id} has no primary source`);
  }
  return { primary, mirrors };
}

function sourceWatchLink(source: VideoSource): VideoWatchLink {
  return { platform: source.platform, url: source.url };
}

function embedBaseUrl(entry: VideoEntry, source: VideoSource): string | null {
  if (source.embed_url !== undefined) return source.embed_url;
  if (source.platform !== "youtube") return null;
  if (source.id === undefined) {
    throw new Error(`YouTube video entry ${entry.id} has no source id`);
  }
  return `https://www.youtube-nocookie.com/embed/${source.id}`;
}
