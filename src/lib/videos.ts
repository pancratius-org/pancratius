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

import { getCollection, render, type CollectionEntry } from "astro:content";

import { DEFAULT_LOCALE, LOCALE_META, LOCALES, type Locale } from "./i18n";
import { layoutFor } from "./video-format";

// Re-export the pure formatter so callers `import { formatDuration } from "@/lib/videos"`.
export { formatDuration } from "./video-format";

export type VideoEntry = CollectionEntry<"videos">;
export type VideoChannel = CollectionEntry<"videoChannels">;
type VideoPairEntries = Partial<Record<Locale, VideoEntry>> & Record<typeof DEFAULT_LOCALE, VideoEntry>;

/**
 * A video and its translations, keyed by `(kind, number)`. Each locale entry
 * is present in `entries` only when that locale's `.md` file exists; the
 * default-locale entry is guaranteed to exist (enforced in `getAllVideoPairs`).
 */
export interface VideoPair {
  number: number;
  /** Authored entries keyed by locale. `entries[DEFAULT_LOCALE]` always exists. */
  entries: VideoPairEntries;
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

let _pairsCache: VideoPair[] | null = null;
let _channelsCache: VideoChannel[] | null = null;

/** The canonical default-locale entry. Every `VideoPair` has one. */
export function defaultVideoEntry(pair: VideoPair): VideoEntry {
  return pair.entries[DEFAULT_LOCALE];
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
  if (_pairsCache) return _pairsCache;

  const all = await getCollection("videos");
  const pairs = videoPairsFromBuckets(videoBuckets(all));

  if (pairs.length > 0) _pairsCache = pairs;
  return pairs;
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
  const pairEntries: VideoPairEntries = { ...entries, [DEFAULT_LOCALE]: canonical };
  return { number, entries: pairEntries };
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
  return pair.entries[locale] ?? null;
}

function localizedVideoPair(pair: VideoPair, locale: Locale): LocalizedVideoPair | null {
  const entry = entryForAuthoredVideoLocale(pair, locale);
  return entry ? { pair, entry, locale } : null;
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
  return LOCALES.flatMap(locale => {
    const item = localizedVideoPair(pair, locale);
    return item ? [item] : [];
  });
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
    const entry = pair.entries[current];
    if (entry) return { entry, linkLocale: current };
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

export async function getChannels(): Promise<VideoChannel[]> {
  if (_channelsCache) return _channelsCache;
  const channels = await getCollection("videoChannels");
  if (channels.length > 0) _channelsCache = channels;
  return channels;
}

async function findChannel(key: string): Promise<VideoChannel | null> {
  const all = await getChannels();
  return all.find(c => c.id === key) ?? null;
}

/** The channel a video's primary source attributes itself to, if any. */
export async function channelForEntry(entry: VideoEntry): Promise<VideoChannel | null> {
  const primary = entry.data.sources[0];
  if (!primary?.channel) return null;
  return findChannel(primary.channel);
}

// ─────────────────────────────────────────────────────────────────────
// Cover resolution. Mirrors `works.ts` — videos use the same on-disk shape
// (`cover.<lang>.<ext>` in the bundle) so the rule is the same; the cache
// just needs to glob a different content root.
// ─────────────────────────────────────────────────────────────────────

const ALLOWED_COVER_RE = new RegExp(
  `^\\./cover\\.(${LOCALES.join("|")})\\.(jpe?g|png|webp|avif)$`,
  "i",
);

const VIDEO_COVER_URLS = import.meta.glob<string>(
  "/src/content/videos/**/cover.*.{jpg,jpeg,png,webp,avif}",
  { eager: true, query: "?url", import: "default" },
);

interface VideoCoverRef { rel: string; lang: Locale; ext: string; }

function parseVideoCover(value: string | null | undefined): VideoCoverRef | null {
  if (!value) return null;
  const match = ALLOWED_COVER_RE.exec(value.trim());
  if (!match) {
    throw new Error(
      `Video cover path ${JSON.stringify(value)} violates asset-naming policy. ` +
      `Expected ./cover.<${LOCALES.join("|")}>.<jpg|png|webp|avif> inside the video bundle.`,
    );
  }
  const lang = match[1];
  const ext = match[2];
  if (lang === undefined || ext === undefined) {
    throw new Error(`Video cover path ${JSON.stringify(value)} matched without locale or extension`);
  }
  return { rel: value.trim(), lang: lang.toLowerCase() as Locale, ext: ext.toLowerCase() };
}

function resolveVideoCover(pair: VideoPair, locale: Locale): VideoCoverRef | null {
  const primary = pair.entries[locale];
  if (primary && primary.data.cover_is_placeholder !== true) {
    const ref = parseVideoCover(primary.data.cover);
    if (ref) return ref;
  }
  if (locale !== DEFAULT_LOCALE) {
    const fallback = defaultVideoEntry(pair);
    const ref = parseVideoCover(fallback.data.cover);
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

export function videoCoverAbsoluteUrl(
  site: URL | undefined,
  pair: VideoPair,
  locale: Locale,
): string | null {
  const rel = videoCoverAssetUrl(pair, locale);
  if (!rel || !site) return rel;
  return new URL(rel, site).toString();
}

// ─────────────────────────────────────────────────────────────────────
// Layout decision — compact vs blog.
//
// Default: derive from body density. `compact` for empty / sparse bodies
// (just an embed + frontmatter meta); `blog` for substantive commentary.
// An explicit `layout:` in frontmatter overrides.
//
// Heuristic: `blog` iff the rendered body has at least one heading OR ≥600
// characters of raw text. The threshold matches "this is a real post" rather
// than "the editor jotted one line." The numbers are tuned to the same
// register as `<Prose>` `data-leadable` (~300 chars for a lead paragraph).
// ─────────────────────────────────────────────────────────────────────

export type VideoLayout = "compact" | "blog";

export async function layoutForEntry(entry: VideoEntry): Promise<VideoLayout> {
  if (entry.data.layout) return entry.data.layout;
  const { headings } = await render(entry);
  return layoutFor(headings.length, entry.body ?? "");
}

// ─────────────────────────────────────────────────────────────────────
// Embed URL helpers.
// ─────────────────────────────────────────────────────────────────────

/** Primary embed URL for a video. Prefers explicit `embed_url`, else derives. */
export function embedUrlFor(entry: VideoEntry): string | null {
  const src = entry.data.sources[0];
  if (!src) return null;
  if (src.embed_url) return src.embed_url;
  if (src.platform === "youtube" && src.id) {
    return `https://www.youtube-nocookie.com/embed/${src.id}`;
  }
  return null;
}

/** Source-platform watch URL for a video's primary source. */
export function watchUrlFor(entry: VideoEntry): string {
  return entry.data.sources[0]?.url ?? "";
}

/** Flatten playlist titles into the video's `tags` list (de-duplicated). */
export function videoTags(entry: VideoEntry): readonly string[] {
  const tags = new Set(entry.data.tags);
  for (const p of entry.data.playlists ?? []) tags.add(p.title);
  return [...tags];
}
