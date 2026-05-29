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

/**
 * A video and its translations, keyed by `(kind, number)`. Each locale entry
 * is present in `entries` only when that locale's `.md` file exists; the
 * default-locale entry is guaranteed to exist (enforced in `getAllVideoPairs`).
 */
export interface VideoPair {
  number: number;
  /** Authored entries keyed by locale. `entries[DEFAULT_LOCALE]` always exists. */
  entries: Partial<Record<Locale, VideoEntry>>;
}

let _pairsCache: VideoPair[] | null = null;
let _channelsCache: VideoChannel[] | null = null;

function defaultVideoEntry(pair: VideoPair): VideoEntry {
  const entry = pair.entries[DEFAULT_LOCALE];
  if (!entry) {
    throw new Error(`Video #${pair.number} has no ${DEFAULT_LOCALE} canonical entry`);
  }
  return entry;
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

  const pairs: VideoPair[] = [];
  for (const [number, entries] of buckets) {
    const canonical = entries[DEFAULT_LOCALE];
    if (!canonical) {
      throw new Error(
        `Video #${number} has translations but no ${DEFAULT_LOCALE} canonical entry`,
      );
    }
    pairs.push({ number, entries });
  }

  pairs.sort((a, b) => {
    const ad = defaultVideoEntry(a).data.published_at;
    const bd = defaultVideoEntry(b).data.published_at;
    if (ad !== bd) return ad < bd ? 1 : -1;
    return b.number - a.number;
  });

  if (pairs.length > 0) _pairsCache = pairs;
  return pairs;
}

/**
 * Display-fallback selector: the entry to render for `locale`, walking the
 * registry's per-locale `fallback` chain. Mirrors `entryForLocale` in
 * `works.ts`. USE FOR DISPLAY ONLY — routes/downloads must read
 * `pair.entries[locale]` directly to decide existence.
 */
export function videoEntryForLocale(pair: VideoPair, locale: Locale): VideoEntry {
  const seen = new Set<Locale>();
  let current: Locale = locale;
  while (!seen.has(current)) {
    const entry = pair.entries[current];
    if (entry) return entry;
    seen.add(current);
    const next = LOCALE_META[current].fallback;
    if (next === current) break;
    current = next;
  }
  return defaultVideoEntry(pair);
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
    const fallback = pair.entries[DEFAULT_LOCALE];
    const ref = parseVideoCover(fallback?.data.cover);
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
  const tags = new Set(entry.data.tags ?? []);
  for (const p of entry.data.playlists ?? []) tags.add(p.title);
  return [...tags];
}
