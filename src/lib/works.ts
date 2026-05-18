// Work discovery, language pairing, and cover/asset resolution.
//
// Route files import from here, not from `astro:content` directly. The
// invariant identity rule (kind, number) lives in one place so that pairing
// EN translations, materializing graph neighbors, and validating cross_refs
// all use the same key.

import { resolve as resolvePath } from "node:path";

import { getCollection, type CollectionEntry } from "astro:content";

import type { Locale, WorkKind } from "./i18n";
import { workUrl } from "./i18n";

const REPO_ROOT = process.cwd();

export type WorkEntry =
  | CollectionEntry<"books">
  | CollectionEntry<"poetry">
  | CollectionEntry<"projects">;

const COLLECTION_OF: Record<WorkKind, "books" | "poetry" | "projects"> = {
  book:    "books",
  poem:    "poetry",
  project: "projects",
};

/**
 * A work and its translations, keyed by `(kind, number)`. Each language entry
 * is present only when that language file exists.
 */
export interface WorkPair {
  kind:   WorkKind;
  number: number;
  /** Canonical RU file — every work must have one. */
  ru: WorkEntry;
  /** Translation, if authored. */
  en: WorkEntry | null;
}

function workKey(entry: WorkEntry): string {
  return `${entry.data.kind}:${entry.data.number}`;
}

let _pairsCache: WorkPair[] | null = null;

/**
 * All works in the corpus, paired across languages by `(kind, number)`.
 * Throws if any non-RU translation lacks a RU counterpart — the RU file is
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

  const buckets = new Map<string, { ru?: WorkEntry; en?: WorkEntry }>();
  for (const entry of all) {
    const key = workKey(entry);
    const bucket = buckets.get(key) ?? {};
    bucket[entry.data.lang] = entry;
    buckets.set(key, bucket);
  }

  const pairs: WorkPair[] = [];
  for (const [key, { ru, en }] of buckets) {
    if (!ru) {
      throw new Error(`Work ${key} has translations but no RU canonical entry`);
    }
    pairs.push({
      kind:   ru.data.kind as WorkKind,
      number: ru.data.number,
      ru,
      en: en ?? null,
    });
  }

  pairs.sort((a, b) => {
    if (a.kind !== b.kind) return a.kind.localeCompare(b.kind);
    return a.number - b.number;
  });

  _pairsCache = pairs;
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
    const entry = locale === "ru" ? pair.ru : pair.en;
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
  return target === "ru" ? pair.ru : pair.en;
}

// ─────────────────────────────────────────────────────────────────────
// Cover and body-image resolution.
//
// Per the asset-naming policy, a cover lives at `content/<work>/cover.<lang>.<ext>`
// and is referenced from frontmatter as a relative path like `./cover.ru.jpg`.
// We reject `/media/<hash>` shapes here so the rule has a single enforcement point.
// ─────────────────────────────────────────────────────────────────────

const ALLOWED_COVER_RE = /^\.\/cover\.(ru|en)\.(jpe?g|png|webp|avif|svg)$/i;

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
      `Expected ./cover.<ru|en>.<jpg|png|webp|avif> inside the work bundle.`,
    );
  }
  return {
    rel:  value.trim(),
    lang: match[1].toLowerCase() as Locale,
    ext:  match[2].toLowerCase(),
  };
}

/**
 * Resolve a cover for a `(kind, slug, locale)` triple, falling back to the RU
 * cover when the locale-specific one isn't authored or is a placeholder.
 * Returns null if no cover frontmatter exists at all (we treat that as "draw
 * an empty card with a subtle placeholder" in the UI).
 *
 * `cover_is_placeholder: true` on an entry counts as "no real cover yet" and
 * triggers the RU fallback.
 */
export async function resolveCover(pair: WorkPair, locale: Locale): Promise<CoverRef | null> {
  const primary = locale === "en" ? pair.en : pair.ru;
  if (primary && primary.data.cover_is_placeholder !== true) {
    const ref = parseCoverRef(primary.data.cover);
    if (ref) return ref;
  }
  if (locale === "en") {
    const ref = parseCoverRef(pair.ru.data.cover);
    if (ref) return ref;
  }
  return null;
}

/**
 * Absolute path on disk for a cover ref, used by the build pipeline to feed
 * into Astro's image helpers. Astro components should prefer
 * `import.meta.glob` for this, but exposing the path makes it available to
 * download endpoints and to OG image emission.
 */
export function coverDiskPath(pair: WorkPair, cover: CoverRef): string {
  const kindFolder = COLLECTION_OF[pair.kind];
  // Astro entry IDs are <work>--<lang>; the work folder is the first segment.
  const workFolder = pair.ru.id.split("--")[0];
  return resolvePath(
    REPO_ROOT,
    "content", kindFolder, workFolder, cover.rel.replace(/^\.\//, ""),
  );
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
    const display = (locale === "en" ? pair.en : pair.ru) ?? pair.ru;
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
// `WorkEntry` is a union; not every kind has `tags`, `abstract`, or `date`.
// Route files use these helpers instead of direct property access so they
// don't have to repeat the same `in`-guard at every callsite.
// ─────────────────────────────────────────────────────────────────────

export function workTags(entry: WorkEntry): readonly string[] {
  return "tags" in entry.data ? entry.data.tags : [];
}

export function workAbstract(entry: WorkEntry): string | undefined {
  return "abstract" in entry.data ? entry.data.abstract : undefined;
}

export function poemDate(entry: WorkEntry): string | null {
  if (!("date" in entry.data)) return null;
  return entry.data.date ?? null;
}

export { COLLECTION_OF };
