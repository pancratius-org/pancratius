// Послания discovery, language pairing, and related-video resolution; the
// месяцеслов calendar model is instantiated for this kind and re-exported below.
//
// Послания are a routed kind but NOT a corpus work — same stance as videos.
// They live outside the `WorkPair` machinery in their own `MessagePair` type:
// the pairing rule is the same shape (`(kind, number)`), but posts carry no
// download matrix and no converter, so folding them into `works.ts` would drag
// non-work concerns into the work-pair API. They stay separate by design.
//
// Route files import from here; they never touch `astro:content` directly so
// the (kind, number) invariant stays in one place.

import { getCollection, type CollectionEntry } from "astro:content";

import { DEFAULT_LOCALE, LOCALES, type Locale } from "./i18n";
import { getAllVideoPairs, type VideoPair } from "./videos";
// The месяцеслов calendar model lives in `./message-calendar` (pure, no
// `astro:content`, generic, unit-testable); the wrappers below instantiate it
// for the corpus payload — the `videos.ts`/`video-format.ts` split, mirrored.
import {
  buildCalendar as buildCalendarOf,
  groupByMonthDesc as groupByMonthDescOf,
  type Calendar,
  type DatedItem,
  type MonthGroup,
} from "./message-calendar";

export type MessageEntry = CollectionEntry<"messages">;
type MessagePairEntries = Partial<Record<Locale, MessageEntry>> & Record<typeof DEFAULT_LOCALE, MessageEntry>;

/**
 * A post and its translations, keyed by `(kind, number)`. Each locale entry is
 * present in `entries` only when that locale's `.md` file exists; the
 * default-locale entry is guaranteed to exist (enforced in `getAllMessagePairs`).
 */
export interface MessagePair {
  number: number;
  /** Authored entries keyed by locale. `entries[DEFAULT_LOCALE]` always exists. */
  entries: MessagePairEntries;
}

export interface LocalizedMessagePair {
  pair:   MessagePair;
  entry:  MessageEntry;
  locale: Locale;
}

let _pairsCache: MessagePair[] | null = null;

/** The canonical default-locale entry. Every `MessagePair` has one. */
export function defaultMessageEntry(pair: MessagePair): MessageEntry {
  return pair.entries[DEFAULT_LOCALE];
}

/**
 * All Послания, paired across languages by `number`. Throws if any post lacks a
 * default-locale entry — the default-locale file is the canonical anchor.
 *
 * Sort order: most recently published first (`published_at` desc, then `number`
 * desc as tiebreaker). The archive and the detail-page sidebar lead with the
 * newest letter, which is what a dated section is for.
 */
export async function getAllMessagePairs(): Promise<MessagePair[]> {
  if (_pairsCache) return _pairsCache;
  const all = await getCollection("messages");
  const pairs = messagePairsFromBuckets(messageBuckets(all));
  if (pairs.length > 0) _pairsCache = pairs;
  return pairs;
}

function messageBuckets(all: readonly MessageEntry[]): Map<number, Partial<Record<Locale, MessageEntry>>> {
  const buckets = new Map<number, Partial<Record<Locale, MessageEntry>>>();
  for (const entry of all) {
    const bucket = buckets.get(entry.data.number) ?? {};
    const lang = entry.data.lang;
    const existing = bucket[lang];
    if (existing) {
      throw new Error(
        `Duplicate послание #${entry.data.number}/${lang}: ${existing.id} and ${entry.id} share a (number, lang)`,
      );
    }
    bucket[lang] = entry;
    buckets.set(entry.data.number, bucket);
  }
  return buckets;
}

function messagePairsFromBuckets(buckets: ReadonlyMap<number, Partial<Record<Locale, MessageEntry>>>): MessagePair[] {
  return [...buckets.entries()]
    .map(([number, entries]) => messagePairFromBucket(number, entries))
    .sort(compareMessagePairs);
}

function messagePairFromBucket(number: number, entries: Partial<Record<Locale, MessageEntry>>): MessagePair {
  const canonical = entries[DEFAULT_LOCALE];
  if (!canonical) {
    throw new Error(`Послание #${number} has translations but no ${DEFAULT_LOCALE} canonical entry`);
  }
  const pairEntries: MessagePairEntries = { ...entries, [DEFAULT_LOCALE]: canonical };
  return { number, entries: pairEntries };
}

/** Newest first by `published_at`, then by `number` as a stable tiebreaker. */
function compareMessagePairs(a: MessagePair, b: MessagePair): number {
  const ad = defaultMessageEntry(a).data.published_at;
  const bd = defaultMessageEntry(b).data.published_at;
  if (ad !== bd) return ad < bd ? 1 : -1;
  return b.number - a.number;
}

/**
 * Authored-locale selector for route existence. Returns null when this pair has
 * no authored file in `locale`; it deliberately does not fall back.
 */
export function entryForAuthoredMessageLocale(pair: MessagePair, locale: Locale): MessageEntry | null {
  return pair.entries[locale] ?? null;
}

function localizedMessagePair(pair: MessagePair, locale: Locale): LocalizedMessagePair | null {
  const entry = entryForAuthoredMessageLocale(pair, locale);
  return entry ? { pair, entry, locale } : null;
}

/** Pairs that have an authored entry in `locale`, in canonical (newest) order. */
export function localizedMessagePairs(
  pairs: readonly MessagePair[],
  locale: Locale,
): LocalizedMessagePair[] {
  const localized: LocalizedMessagePair[] = [];
  for (const pair of pairs) {
    const item = localizedMessagePair(pair, locale);
    if (item) localized.push(item);
  }
  return localized;
}

/** Every authored locale of a pair — drives hreflang siblings. */
export function authoredMessagePairs(pair: MessagePair): LocalizedMessagePair[] {
  return LOCALES.flatMap(locale => {
    const item = localizedMessagePair(pair, locale);
    return item ? [item] : [];
  });
}

/** True when at least one post is authored in `locale` — gates the nav item. */
export async function hasMessagesInLocale(locale: Locale): Promise<boolean> {
  const pairs = await getAllMessagePairs();
  return localizedMessagePairs(pairs, locale).length > 0;
}

export function messageTags(entry: MessageEntry): readonly string[] {
  return entry.data.tags;
}

// ─────────────────────────────────────────────────────────────────────
// Related videos. A post names the videos it accompanies by editorial
// `number`; resolve those to VideoPairs in authored order (unlike the
// recency-sorted pair lists elsewhere), silently dropping any number with no
// matching video so an edited reference never fails the build.
// ─────────────────────────────────────────────────────────────────────

export async function relatedVideosFor(entry: MessageEntry): Promise<VideoPair[]> {
  const numbers = entry.data.related_videos ?? [];
  if (numbers.length === 0) return [];
  const all = await getAllVideoPairs();
  const byNumber = new Map(all.map(p => [p.number, p]));
  const out: VideoPair[] = [];
  for (const n of numbers) {
    const found = byNumber.get(n);
    if (found) out.push(found);
  }
  return out;
}

export { todayISO } from "./message-calendar";
export type { CalendarDay, MonthGroup } from "./message-calendar";

/** A post placed on the месяцеслов: its date, with `number` as the within-day
 *  ordering key (newest first), carrying the pair itself for rendering. */
function toDated(pair: LocalizedMessagePair): DatedItem<LocalizedMessagePair> {
  return { iso: pair.entry.data.published_at, order: pair.pair.number, value: pair };
}

export function buildCalendar(
  localized: readonly LocalizedMessagePair[],
  today: string,
): Calendar<LocalizedMessagePair> {
  return buildCalendarOf(localized.map(toDated), today);
}

export function groupByMonthDesc(
  localized: readonly LocalizedMessagePair[],
): MonthGroup<LocalizedMessagePair>[] {
  return groupByMonthDescOf(localized.map(toDated));
}
