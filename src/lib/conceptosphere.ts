// Adapter for the public graph payloads and the merged "Похожие книги" ranker.
//
// `data/pancratius-{concepts,books}-graph.json` is the contract. The
// embedding intermediate (`data/conceptosphere-embed.json`) and the embedding
// cache (`data/conceptosphere-embed-cache/`) are build-only — production code
// never reads them, and they are never copied into `dist/`.

import { readFileSync } from "node:fs";
import { resolve as resolvePath } from "node:path";

import type { Locale, WorkKind } from "./i18n";
import { workUrl } from "./i18n";
import type { WorkEntry, WorkPair } from "./works";
import { crossRefKeys, findPair, pairKey } from "./works";

const REPO_ROOT = process.cwd();

// ─────────────────────────────────────────────────────────────────────
// Wire types — mirror what scripts/conceptosphere.py emits.
// Only the fields production reads are typed; the rest stays untyped.
// ─────────────────────────────────────────────────────────────────────

export interface BooksGraphCommunity {
  id:          number;
  label:       string;
  size:        number;
  // The payload also carries `color_index`, but production picks colour from
  // `PALETTE[id % PALETTE.length]` in both the desktop runtime and the
  // mobile list so the two surfaces never disagree. We deliberately omit
  // `color_index` from the typed loader so future code doesn't pick it up
  // and drift from that contract; the field can be reintroduced if we ever
  // want to delegate palette choice back to the payload.
}

export interface SimilarRef {
  slug:   string;
  kind:   WorkKind;
  title:  string;
  weight: number;
}

export interface BooksGraphNode {
  id:                 string;
  slug:               string;
  number:             number;
  label:              string;
  title:              string;
  tags:               string[];
  frequency:          number;
  degree:             number;
  weighted_degree:    number;
  centrality:         number;
  community:          number;
  top_concepts:       { label: string; lemma: string; count: number }[];
  top_similar:        SimilarRef[];
  top_similar_embed?: SimilarRef[];
}

export interface BooksGraphEdge {
  source: string;
  target: string;
  weight: number;
}

export interface BooksGraph {
  generated_at: string;
  mode:         "books";
  params:       Record<string, unknown>;
  stats:        Record<string, unknown>;
  communities:  BooksGraphCommunity[];
  nodes:        BooksGraphNode[];
  edges:        BooksGraphEdge[];
}

export interface ConceptsGraphNode {
  id:              string;
  label:           string;
  lemma:           string;
  frequency:       number;
  degree:          number;
  weighted_degree: number;
  centrality:      number;
  community:       number;
  top_books:       { slug: string; kind: "book"; title: string; count: number }[];
}

export interface ConceptsGraphEdge {
  source: string;
  target: string;
  weight: number;
  npmi?:  number;
}

export interface ConceptsGraph {
  generated_at: string;
  mode?:        "concepts";
  params:       Record<string, unknown>;
  stats:        Record<string, unknown>;
  communities:  BooksGraphCommunity[];
  nodes:        ConceptsGraphNode[];
  edges:        ConceptsGraphEdge[];
}

// ─────────────────────────────────────────────────────────────────────
// Loaders. Read once at build time, cache the parsed payload.
// ─────────────────────────────────────────────────────────────────────

let _booksGraph:    BooksGraph | null = null;
let _conceptsGraph: ConceptsGraph | null = null;

export function loadBooksGraph(): BooksGraph {
  if (_booksGraph) return _booksGraph;
  const raw = readFileSync(resolvePath(REPO_ROOT, "data", "pancratius-books-graph.json"), "utf-8");
  _booksGraph = JSON.parse(raw) as BooksGraph;
  return _booksGraph;
}

export function loadConceptsGraph(): ConceptsGraph {
  if (_conceptsGraph) return _conceptsGraph;
  const raw = readFileSync(resolvePath(REPO_ROOT, "data", "pancratius-concepts-graph.json"), "utf-8");
  _conceptsGraph = JSON.parse(raw) as ConceptsGraph;
  return _conceptsGraph;
}

// ─────────────────────────────────────────────────────────────────────
// Books-graph lookup by `slug` (the per-language slug). The graph is built
// from RU content, so its node IDs are RU slugs.
// ─────────────────────────────────────────────────────────────────────

let _bookNodeBySlug: Map<string, BooksGraphNode> | null = null;
let _bookNodeByNumber: Map<number, BooksGraphNode> | null = null;

function indexBookNodes(): void {
  if (_bookNodeBySlug && _bookNodeByNumber) return;
  const graph = loadBooksGraph();
  _bookNodeBySlug   = new Map();
  _bookNodeByNumber = new Map();
  for (const node of graph.nodes) {
    _bookNodeBySlug.set(node.slug, node);
    _bookNodeByNumber.set(node.number, node);
  }
}

export function bookNode(numberOrSlug: number | string): BooksGraphNode | null {
  indexBookNodes();
  if (typeof numberOrSlug === "number") return _bookNodeByNumber!.get(numberOrSlug) ?? null;
  return _bookNodeBySlug!.get(numberOrSlug) ?? null;
}

// ─────────────────────────────────────────────────────────────────────
// Merged Похожие книги ranker.
//
// Inputs per book node: `top_similar` (TF-IDF cosine) and `top_similar_embed`
// (semantic, optional). Both are pre-sorted by weight descending. We fuse
// them into one ranked list. Rules:
//
//   1. Normalize each input list to [0, 1] over its own range.
//   2. Score = max(tfidfNorm, embedNorm) + bonus when both lists rank the
//      same target. Bonus is capped so a very strong single-source pick can
//      still beat a moderate double-source pick.
//   3. Mark convergent items (present in both lists, both passing a minimum
//      threshold) with `convergent: true` → ★ in UI.
//   4. Exclude the current work and anything already in authored cross_refs.
//   5. Sort by score desc, stable on ties.
//
// The result is a small list (default top 6).
// ─────────────────────────────────────────────────────────────────────

export interface SimilarPickInput {
  /** The current book entry being viewed. Used to skip self and respect cross_refs. */
  entry:  WorkEntry;
  /** Locale used to build URLs on each pick. */
  locale: Locale;
  /** Optional limit; default 6. */
  limit?: number;
}

export interface SimilarPick {
  pair:       WorkPair;
  /** Localised work URL for the active page locale. */
  url:        string;
  convergent: boolean;
}

const CONVERGENCE_BONUS = 0.15;             // capped: weaker than the gap between strong and weak single-source picks
const CONVERGENCE_THRESHOLD = 0.25;          // normalized score floor that qualifies as a "real" pick on a list

export async function getMergedSimilar(input: SimilarPickInput): Promise<SimilarPick[]> {
  const { entry, locale, limit = 6 } = input;
  if (entry.data.kind !== "book") return [];

  const node = bookNode(entry.data.number);
  if (!node) return [];

  const tfidf = node.top_similar ?? [];
  const embed = node.top_similar_embed ?? [];

  const tfidfMax = tfidf[0]?.weight ?? 1;
  const embedMax = embed[0]?.weight ?? 1;

  type Bucket = {
    ref:        SimilarRef;
    tfidfNorm:  number;
    embedNorm:  number;
    seenIn:     Set<"tfidf" | "embed">;
  };
  const buckets = new Map<string, Bucket>();

  function bucketKey(ref: SimilarRef): string {
    return `${ref.kind}:${ref.slug}`;
  }

  for (const ref of tfidf) {
    const key = bucketKey(ref);
    const norm = tfidfMax > 0 ? ref.weight / tfidfMax : 0;
    const bucket = buckets.get(key) ?? { ref, tfidfNorm: 0, embedNorm: 0, seenIn: new Set() };
    bucket.tfidfNorm = Math.max(bucket.tfidfNorm, norm);
    bucket.seenIn.add("tfidf");
    buckets.set(key, bucket);
  }
  for (const ref of embed) {
    const key = bucketKey(ref);
    const norm = embedMax > 0 ? ref.weight / embedMax : 0;
    const bucket = buckets.get(key) ?? { ref, tfidfNorm: 0, embedNorm: 0, seenIn: new Set() };
    bucket.embedNorm = Math.max(bucket.embedNorm, norm);
    bucket.seenIn.add("embed");
    buckets.set(key, bucket);
  }

  // Exclude the current work and anything in authored См. также.
  const exclude = crossRefKeys(entry);
  const selfSlug = node.slug;
  const scored: { pick: SimilarPick; sortKey: number; bucket: Bucket }[] = [];

  for (const bucket of buckets.values()) {
    // «Похожие книги» is a books-only surface per docs/conceptosphere.md.
    // top_similar_embed leaks poems/projects into the input lists; filter them out.
    if (bucket.ref.kind !== "book") continue;
    if (bucket.ref.slug === selfSlug) continue;

    const neighborNode = bookNode(bucket.ref.slug);
    if (!neighborNode) continue;
    if (exclude.has(pairKey("book", neighborNode.number))) continue;

    // Resolve through `(kind, number)` so we use the *localized* slug, title,
    // and cover. If the EN page doesn't exist on an EN reader's surface, skip
    // the pick rather than emit a dead link to /en/books/<ru-slug>/.
    const pair = await findPair("book", neighborNode.number);
    if (!pair) continue;
    // Existence: skip the pick if the page doesn't exist in this locale rather
    // than emit a dead link to /<locale>/books/<default-locale-slug>/.
    const display = pair.entries[locale];
    if (!display) continue;

    const convergent =
      bucket.seenIn.has("tfidf") &&
      bucket.seenIn.has("embed") &&
      bucket.tfidfNorm >= CONVERGENCE_THRESHOLD &&
      bucket.embedNorm >= CONVERGENCE_THRESHOLD;

    const baseScore = Math.max(bucket.tfidfNorm, bucket.embedNorm);
    const bonus = convergent ? CONVERGENCE_BONUS : 0;
    const score = baseScore + bonus;

    scored.push({
      pick: {
        pair,
        url:        workUrl("book", display.data.slug, locale),
        convergent,
      },
      sortKey: score,
      bucket,
    });
  }

  // Stable sort by score desc; secondary tiebreak by tfidfNorm so single-source
  // strong-TF-IDF wins over weaker single-source semantic on the rare tie.
  scored.sort((a, b) => {
    if (b.sortKey !== a.sortKey) return b.sortKey - a.sortKey;
    return b.bucket.tfidfNorm - a.bucket.tfidfNorm;
  });

  return scored.slice(0, limit).map(s => s.pick);
}
