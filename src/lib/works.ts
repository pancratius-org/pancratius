// Work discovery, language pairing, and cover/asset resolution.
//
// Route files import from here, not from `astro:content` directly. The
// invariant identity rule (kind, number) lives in one place so that pairing
// EN translations, materializing graph neighbors, and validating cross_refs
// all use the same key.

import { getCollection, type CollectionEntry } from "astro:content";

import type { Locale, WorkKind } from "./i18n";
import { DEFAULT_LOCALE, LOCALE_META, LOCALES, workUrl } from "./i18n";
import { SEGMENT_OF } from "./kinds";

export type WorkEntry =
  | CollectionEntry<"books">
  | CollectionEntry<"poetry">
  | CollectionEntry<"projects">;

/**
 * Work kind → content collection name. These segments double as both the
 * collection names and the URL segments, so this is just the canonical
 * `SEGMENT_OF` map re-exported under the name route files already import.
 */
export const COLLECTION_OF = SEGMENT_OF;

/**
 * A work and its translations, keyed by `(kind, number)`. Each locale entry is
 * present in `entries` only when that locale's file exists; the default-locale
 * entry is guaranteed to exist (enforced at build in `getAllWorkPairs`).
 */
export interface WorkPair {
  kind:    WorkKind;
  number:  number;
  /** Authored entries keyed by locale. `entries[DEFAULT_LOCALE]` always exists. */
  entries: Partial<Record<Locale, WorkEntry>>;
}

/**
 * Display-fallback selector: the entry to render for `locale`, walking the
 * registry's per-locale `fallback` chain (locale → LOCALE_META[locale].fallback
 * → … → DEFAULT_LOCALE) until an authored entry is found. The chain always
 * terminates because DEFAULT_LOCALE's fallback is itself; the visited-set guard
 * also breaks any accidental cycle. `pair.entries[DEFAULT_LOCALE]` is
 * guaranteed to exist (enforced in `getAllWorkPairs`), so this never returns
 * undefined.
 *
 * USE FOR DERIVED DISPLAY DATA ONLY — cover resolution, cross-ref title
 * display, JSON-LD. Do NOT use it to decide whether a route or download
 * exists: those must read `pair.entries[locale]` directly so an `/en/…` page
 * never renders default-locale content for a locale that wasn't authored.
 */
export function entryForLocale(pair: WorkPair, locale: Locale): WorkEntry {
  const seen = new Set<Locale>();
  let current: Locale = locale;
  while (!seen.has(current)) {
    const entry = pair.entries[current];
    if (entry) return entry;
    seen.add(current);
    const next = LOCALE_META[current].fallback;
    if (next === current) break;  // DEFAULT_LOCALE.fallback === DEFAULT_LOCALE
    current = next;
  }
  // Chain exhausted without an authored entry — fall back to the canonical
  // default-locale entry, which getAllWorkPairs guarantees exists.
  return pair.entries[DEFAULT_LOCALE]!;
}

function workKey(entry: WorkEntry): string {
  return `${entry.data.kind}:${entry.data.number}`;
}

let _pairsCache: WorkPair[] | null = null;

/**
 * All works in the corpus, paired across languages by `(kind, number)`.
 * Throws if any work lacks a default-locale entry — the default-locale file is
 * the canonical anchor for every work.
 */
export async function getAllWorkPairs(): Promise<WorkPair[]> {
  if (_pairsCache) return _pairsCache;

  const [books, poetry, projects] = await Promise.all([
    getCollection("books"),
    getCollection("poetry"),
    getCollection("projects"),
  ]);
  const all: WorkEntry[] = [...books, ...poetry, ...projects];

  const buckets = new Map<string, Partial<Record<Locale, WorkEntry>>>();
  for (const entry of all) {
    const key = workKey(entry);
    const bucket = buckets.get(key) ?? {};
    bucket[entry.data.lang] = entry;
    buckets.set(key, bucket);
  }

  const pairs: WorkPair[] = [];
  for (const [key, entries] of buckets) {
    const canonical = entries[DEFAULT_LOCALE];
    if (!canonical) {
      throw new Error(
        `Work ${key} has translations but no ${DEFAULT_LOCALE} canonical entry`,
      );
    }
    pairs.push({
      kind:    canonical.data.kind as WorkKind,
      number:  canonical.data.number,
      entries,
    });
  }

  pairs.sort((a, b) => {
    if (a.kind !== b.kind) return a.kind.localeCompare(b.kind);
    return a.number - b.number;
  });

  // Never cache an empty result — it only means content wasn't synced yet
  // (dev-server startup race). Caching [] poisons every later call.
  if (pairs.length > 0) _pairsCache = pairs;
  return pairs;
}

export async function getPairsByKind(kind: WorkKind): Promise<WorkPair[]> {
  const all = await getAllWorkPairs();
  return all.filter(p => p.kind === kind);
}

export async function findPair(kind: WorkKind, number: number): Promise<WorkPair | null> {
  const all = await getAllWorkPairs();
  return all.find(p => p.kind === kind && p.number === number) ?? null;
}

/** Look up by per-language slug (the URL slug). */
export async function findEntryBySlug(
  kind: WorkKind,
  slug: string,
  locale: Locale,
): Promise<WorkEntry | null> {
  const all = await getAllWorkPairs();
  for (const pair of all) {
    if (pair.kind !== kind) continue;
    // Existence: only match a slug authored *in this locale*.
    const entry = pair.entries[locale];
    if (entry && entry.data.slug === slug) return entry;
  }
  return null;
}

/** The localized URL for an entry. Uses the entry's own per-language slug. */
export function entryUrl(entry: WorkEntry): string {
  return workUrl(entry.data.kind as WorkKind, entry.data.slug, entry.data.lang as Locale);
}

/** Counterpart entry in the other language, if one exists. */
export async function alternateLanguageEntry(
  entry: WorkEntry,
  target: Locale,
): Promise<WorkEntry | null> {
  if (entry.data.lang === target) return entry;
  const pair = await findPair(entry.data.kind as WorkKind, entry.data.number);
  if (!pair) return null;
  // Existence: the counterpart exists only if `target` was authored.
  return pair.entries[target] ?? null;
}

// ─────────────────────────────────────────────────────────────────────
// Cover resolution.
//
// Per the asset-naming policy, a cover lives at
// `src/content/<kind>/<work>/cover.<lang>.<ext>` and is referenced from
// frontmatter as a relative path like `./cover.ru.jpg`.
// We reject `/media/<hash>` shapes here so the rule has a single enforcement point.
// ─────────────────────────────────────────────────────────────────────

const ALLOWED_COVER_RE = new RegExp(
  `^\\./cover\\.(${LOCALES.join("|")})\\.(jpe?g|png|webp|avif|svg)$`,
  "i",
);

export interface CoverRef {
  /** The relative path as it appears in frontmatter, e.g. `./cover.ru.jpg`. */
  rel: string;
  /** Resolved language hint inferred from the filename. */
  lang: Locale;
  /** File extension lowercased without leading dot. */
  ext: string;
}

/** Parse and validate a `cover:` frontmatter value. Returns null if absent. */
export function parseCoverRef(value: string | null | undefined): CoverRef | null {
  if (!value) return null;
  const match = ALLOWED_COVER_RE.exec(value.trim());
  if (!match) {
    throw new Error(
      `Cover path ${JSON.stringify(value)} violates asset-naming policy. ` +
      `Expected ./cover.<${LOCALES.join("|")}>.<jpg|png|webp|avif> inside the work bundle.`,
    );
  }
  return {
    rel:  value.trim(),
    lang: match[1].toLowerCase() as Locale,
    ext:  match[2].toLowerCase(),
  };
}

/**
 * Resolve a cover for a `(kind, slug, locale)` triple, falling back to the
 * default-locale cover when the locale-specific one isn't authored or is a
 * placeholder. Returns null if no cover frontmatter exists at all (we treat
 * that as "draw an empty card with a subtle placeholder" in the UI).
 *
 * This is DISPLAY data — the fallback to the default-locale cover is intended.
 *
 * `cover_is_placeholder: true` on an entry counts as "no real cover yet" and
 * triggers the default-locale fallback.
 */
export async function resolveCover(pair: WorkPair, locale: Locale): Promise<CoverRef | null> {
  const primary = pair.entries[locale];
  if (primary && primary.data.cover_is_placeholder !== true) {
    const ref = parseCoverRef(primary.data.cover);
    if (ref) return ref;
  }
  if (locale !== DEFAULT_LOCALE) {
    const fallback = pair.entries[DEFAULT_LOCALE];
    const ref = parseCoverRef(fallback?.data.cover);
    if (ref) return ref;
  }
  return null;
}

// ─────────────────────────────────────────────────────────────────────
// Authored cross_refs → См. также.
// ─────────────────────────────────────────────────────────────────────

export interface ResolvedCrossRef {
  target:  WorkPair;
  /** The translation surface used to render the title in the current locale. */
  display: WorkEntry;
  snippet?: string;
  sourceUrl?: string;
}

/**
 * Resolve a work entry's authored `cross_refs` into renderable references.
 * Dangling refs are caught at build time by `scripts/build_slug_map.py`, so
 * any here-and-now miss is an integrity failure worth crashing the build.
 */
export async function resolveCrossRefs(
  entry: WorkEntry,
  locale: Locale,
): Promise<ResolvedCrossRef[]> {
  const refs = entry.data.cross_refs ?? [];
  if (refs.length === 0) return [];
  const resolved: ResolvedCrossRef[] = [];
  for (const ref of refs) {
    const pair = await findPair(ref.target.kind as WorkKind, ref.target.number);
    if (!pair) {
      throw new Error(
        `cross_refs dangling reference from ${entry.id}: ${ref.target.kind} #${ref.target.number} not in corpus`,
      );
    }
    const display = entryForLocale(pair, locale);
    resolved.push({
      target:    pair,
      display,
      snippet:   ref.snippet,
      sourceUrl: ref.source_url,
    });
  }
  return resolved;
}

// Used by `src/lib/conceptosphere.ts` so the merged ranker can exclude
// already-authored См. также picks.
export function crossRefKeys(entry: WorkEntry): Set<string> {
  const refs = entry.data.cross_refs ?? [];
  return new Set(refs.map(r => `${r.target.kind}:${r.target.number}`));
}

export function pairKey(kind: WorkKind, number: number): string {
  return `${kind}:${number}`;
}

// ─────────────────────────────────────────────────────────────────────
// Narrowed accessors for kind-specific frontmatter fields.
//
// `WorkEntry` is a union; only books carry `tags`, only poems carry `date`.
// Route files use these helpers instead of repeating the `in`-guard everywhere.
// ─────────────────────────────────────────────────────────────────────

export function workTags(entry: WorkEntry): readonly string[] {
  return "tags" in entry.data ? entry.data.tags : [];
}

export function poemDate(entry: WorkEntry): string | null {
  if (!("date" in entry.data)) return null;
  return entry.data.date ?? null;
}
