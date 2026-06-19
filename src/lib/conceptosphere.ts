// Adapter for the public graph payloads and the merged "Похожие книги" ranker.
//
// `data/pancratius-{concepts,books}-graph.json` is the contract. The
// embedding intermediate (`data/conceptosphere-embed.json`) and the embedding
// cache (`data/conceptosphere-embed-cache/`) are build-only — production code
// never reads them, and they are never copied into `dist/`.

import { readFileSync } from "node:fs";

import { graphPayloadPath } from "./conceptosphere-payload-path";
import type { Locale, RoutedKind } from "./i18n";
import { DEFAULT_LOCALE, workUrl } from "./i18n";
import type { WorkEntry, WorkPair } from "./works";
import { crossRefKeys, entryForAuthoredLocale, findPair, pairKey } from "./works";

const REPO_ROOT = process.cwd();

// ─────────────────────────────────────────────────────────────────────
// Wire types — mirror what `pancratius conceptosphere graph generate` emits.
// Only the fields production reads are typed; the rest stays untyped.
// ─────────────────────────────────────────────────────────────────────

interface BooksGraphCommunity {
  id:          number;
  label:       string;
  size:        number;
  top_concepts?: ConceptRef[];
  // The payload also carries `color_index`, but production picks colour from
  // `PALETTE[id % PALETTE.length]` in both the desktop runtime and the
  // mobile list so the two surfaces never disagree. We deliberately omit
  // `color_index` from the typed loader so future code doesn't pick it up
  // and drift from that contract; the field can be reintroduced if we ever
  // want to delegate palette choice back to the payload.
}

interface ConceptRef {
  concept_id?: string;
  label:       string;
  lemma?:      string;
  count?:      number;
  score?:      number;
  coverage?:   number;
  weight?:     number;
  untranslated?: boolean;
}

interface SimilarRef {
  slug:   string;
  kind:   RoutedKind;
  title:  string;
  weight: number;
  shared_concepts?: ConceptRef[];
}

interface BooksGraphNode {
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
  top_concepts:       ConceptRef[];
  top_similar:        SimilarRef[];
  top_similar_embed?: SimilarRef[];
}

interface BooksGraphEdge {
  source: string;
  target: string;
  weight: number;
  shared_concepts?: ConceptRef[];
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

interface ConceptsGraphNode {
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

interface ConceptsGraphEdge {
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
// Loaders. Read once at build time, cache the parsed payload per locale.
//
// The default (RU) locale reads the un-suffixed source graph under `data/`.
// A non-default locale reads the per-locale payload the build join already
// emitted under `public/data/` (`build/copy-graph-payloads.ts` produces
// `pancratius-*-graph.<locale>.json` = RU topology ⋈ the authored overlay).
// That build-time join is the ONLY bridge: this loader CONSUMES its output,
// it does not re-join. The desktop graph fetches the same `.<locale>.json`,
// so the server-rendered mobile list and the client graph render identical
// labels. A missing localized payload throws (no silent RU fallback under an
// English URL); `npm run generate` emits it before any render.
// ─────────────────────────────────────────────────────────────────────

const _booksGraph = new Map<Locale, BooksGraph>();
const _conceptsGraph = new Map<Locale, ConceptsGraph>();

export function loadBooksGraph(locale: Locale = DEFAULT_LOCALE): BooksGraph {
  const cached = _booksGraph.get(locale);
  if (cached) return cached;
  const path = graphPayloadPath("pancratius-books-graph", locale, REPO_ROOT);
  const graph = JSON.parse(readFileSync(path, "utf-8")) as BooksGraph;
  _booksGraph.set(locale, graph);
  return graph;
}

export function loadConceptsGraph(locale: Locale = DEFAULT_LOCALE): ConceptsGraph {
  const cached = _conceptsGraph.get(locale);
  if (cached) return cached;
  const path = graphPayloadPath("pancratius-concepts-graph", locale, REPO_ROOT);
  const graph = JSON.parse(readFileSync(path, "utf-8")) as ConceptsGraph;
  _conceptsGraph.set(locale, graph);
  return graph;
}

// ─────────────────────────────────────────────────────────────────────
// Books-graph lookup by `slug` (the per-language slug). The graph is built
// from RU content, so its node IDs are RU slugs.
// ─────────────────────────────────────────────────────────────────────

let _bookNodeIndex: {
  bySlug:   Map<string, BooksGraphNode>;
  byNumber: Map<number, BooksGraphNode>;
} | null = null;

function bookNodeIndex(): { bySlug: Map<string, BooksGraphNode>; byNumber: Map<number, BooksGraphNode> } {
  if (_bookNodeIndex) return _bookNodeIndex;
  const graph = loadBooksGraph();
  const bySlug = new Map<string, BooksGraphNode>();
  const byNumber = new Map<number, BooksGraphNode>();
  for (const node of graph.nodes) {
    bySlug.set(node.slug, node);
    byNumber.set(node.number, node);
  }
  _bookNodeIndex = { bySlug, byNumber };
  return _bookNodeIndex;
}

function bookNode(numberOrSlug: number | string): BooksGraphNode | null {
  const index = bookNodeIndex();
  if (typeof numberOrSlug === "number") return index.byNumber.get(numberOrSlug) ?? null;
  return index.bySlug.get(numberOrSlug) ?? null;
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

type SimilarSource = "tfidf" | "embed";

interface SimilarEvidence {
  ref: SimilarRef;
  tfidfNorm: number;
  embedNorm: number;
  sources: Set<SimilarSource>;
  order: number;
}

interface SimilarResolutionContext {
  locale: Locale;
  exclude: ReadonlySet<string>;
  selfSlug: string;
}

interface RankedSimilarPick {
  pick: SimilarPick;
  score: number;
  evidence: SimilarEvidence;
}

export async function getMergedSimilar(input: SimilarPickInput): Promise<SimilarPick[]> {
  const { entry, locale, limit = 6 } = input;
  const node = recommendationBookNode(entry);
  if (!node) return [];

  const ranked = await resolveSimilarPicks(fusedSimilarEvidence(node), {
    locale,
    exclude: crossRefKeys(entry),
    selfSlug: node.slug,
  });

  return ranked
    .sort(compareRankedSimilar)
    .slice(0, limit)
    .map(item => item.pick);
}

function recommendationBookNode(entry: WorkEntry): BooksGraphNode | null {
  if (entry.data.kind !== "book") return null;
  return bookNode(entry.data.number);
}

function fusedSimilarEvidence(node: BooksGraphNode): SimilarEvidence[] {
  const buckets = new Map<string, SimilarEvidence>();
  const order = { next: 0 };
  recordSimilarSource(buckets, order, node.top_similar, "tfidf");
  recordSimilarSource(buckets, order, node.top_similar_embed ?? [], "embed");
  return [...buckets.values()];
}

function recordSimilarSource(
  buckets: Map<string, SimilarEvidence>,
  order: { next: number },
  refs: readonly SimilarRef[],
  source: SimilarSource,
): void {
  const maxWeight = refs[0]?.weight ?? 1;
  for (const ref of refs) {
    const evidence = similarEvidenceBucket(buckets, order, ref);
    recordEvidenceScore(evidence, source, normalizedSimilarWeight(ref, maxWeight));
  }
}

function similarEvidenceBucket(
  buckets: Map<string, SimilarEvidence>,
  order: { next: number },
  ref: SimilarRef,
): SimilarEvidence {
  const key = similarEvidenceKey(ref);
  const existing = buckets.get(key);
  if (existing) return existing;

  const evidence = { ref, tfidfNorm: 0, embedNorm: 0, sources: new Set<SimilarSource>(), order: order.next };
  buckets.set(key, evidence);
  order.next++;
  return evidence;
}

function recordEvidenceScore(
  evidence: SimilarEvidence,
  source: SimilarSource,
  score: number,
): void {
  if (source === "tfidf") evidence.tfidfNorm = Math.max(evidence.tfidfNorm, score);
  else evidence.embedNorm = Math.max(evidence.embedNorm, score);
  evidence.sources.add(source);
}

function normalizedSimilarWeight(ref: SimilarRef, maxWeight: number): number {
  return maxWeight > 0 ? ref.weight / maxWeight : 0;
}

function similarEvidenceKey(ref: SimilarRef): string {
  return `${ref.kind}:${ref.slug}`;
}

async function resolveSimilarPicks(
  evidences: readonly SimilarEvidence[],
  context: SimilarResolutionContext,
): Promise<RankedSimilarPick[]> {
  const ranked: RankedSimilarPick[] = [];
  for (const evidence of evidences) {
    const pick = await resolveSimilarPick(evidence, context);
    if (pick) ranked.push(pick);
  }
  return ranked;
}

async function resolveSimilarPick(
  evidence: SimilarEvidence,
  context: SimilarResolutionContext,
): Promise<RankedSimilarPick | null> {
  const target = similarBookTarget(evidence, context);
  if (!target) return null;

  const pair = await findPair("book", target.number);
  if (!pair) return null;
  const display = entryForAuthoredLocale(pair, context.locale);
  if (!display) return null;

  const rank = similarEvidenceRank(evidence);
  return {
    pick: {
      pair,
      url: workUrl("book", display.data.slug, context.locale),
      convergent: rank.convergent,
    },
    score: rank.score,
    evidence,
  };
}

function similarBookTarget(
  evidence: SimilarEvidence,
  context: SimilarResolutionContext,
): BooksGraphNode | null {
  if (evidence.ref.kind !== "book" || evidence.ref.slug === context.selfSlug) return null;
  const target = bookNode(evidence.ref.slug);
  if (!target) return null;
  return context.exclude.has(pairKey("book", target.number)) ? null : target;
}

function similarEvidenceRank(evidence: SimilarEvidence): { score: number; convergent: boolean } {
  const convergent = isConvergentEvidence(evidence);
  return {
    score: Math.max(evidence.tfidfNorm, evidence.embedNorm) + (convergent ? CONVERGENCE_BONUS : 0),
    convergent,
  };
}

function isConvergentEvidence(evidence: SimilarEvidence): boolean {
  return evidence.sources.has("tfidf")
    && evidence.sources.has("embed")
    && evidence.tfidfNorm >= CONVERGENCE_THRESHOLD
    && evidence.embedNorm >= CONVERGENCE_THRESHOLD;
}

function compareRankedSimilar(a: RankedSimilarPick, b: RankedSimilarPick): number {
  if (b.score !== a.score) return b.score - a.score;
  if (b.evidence.tfidfNorm !== a.evidence.tfidfNorm) {
    return b.evidence.tfidfNorm - a.evidence.tfidfNorm;
  }
  return a.evidence.order - b.evidence.order;
}
