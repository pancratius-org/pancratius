import type { SimilarRef, TopBookRef, TopConceptRef } from "./graph-types.ts";

interface PayloadCommunity {
  id: number;
  label: string;
  size: number;
  top_concepts?: TopConceptRef[];
}

export interface PayloadNode {
  id: string;
  label?: string;
  lemma?: string;
  title?: string;
  slug?: string;
  number?: number;
  tags?: string[];
  community: number;
  frequency?: number;
  centrality?: number;
  degree?: number;
  top_concepts?: TopConceptRef[];
  top_books?: TopBookRef[];
  top_similar?: SimilarRef[];
  top_similar_embed?: SimilarRef[];
}

export interface PayloadEdge {
  source: string;
  target: string;
  weight: number;
  npmi?: number;
  shared_concepts?: TopConceptRef[];
}

export interface GraphPayload {
  stats: Record<string, number | string | undefined>;
  communities: PayloadCommunity[];
  nodes: PayloadNode[];
  edges: PayloadEdge[];
}

export function parseGraphPayload(value: unknown, url: string): GraphPayload {
  if (!isRecord(value) || !Array.isArray(value.communities) || !Array.isArray(value.nodes) || !Array.isArray(value.edges)) {
    throw new Error(`Invalid graph payload: ${url}`);
  }

  return {
    stats: readStats(value.stats),
    communities: parsePayloadArray(value.communities, isCommunity, "community", url),
    nodes: parsePayloadArray(value.nodes, isNode, "node", url),
    edges: parsePayloadArray(value.edges, isEdge, "edge", url),
  };
}

function parsePayloadArray<T>(
  values: readonly unknown[],
  guard: (item: unknown) => item is T,
  label: string,
  url: string,
): T[] {
  return values.map((item, index) => {
    if (!guard(item)) throw new Error(`Invalid graph ${label}[${index}] in ${url}`);
    return item;
  });
}

function readStats(value: unknown): Record<string, number | string | undefined> {
  if (!isRecord(value)) return {};
  const stats: Record<string, number | string | undefined> = {};
  for (const [key, item] of Object.entries(value)) {
    if (typeof item === "number" || typeof item === "string" || item === undefined) {
      stats[key] = item;
    }
  }
  return stats;
}

function isCommunity(value: unknown): value is PayloadCommunity {
  return isRecord(value)
    && isNumber(value.id)
    && typeof value.label === "string"
    && isNumber(value.size)
    && optionalArray(value.top_concepts, isTopConcept);
}

function isNode(value: unknown): value is PayloadNode {
  return isRecord(value)
    && hasNodeIdentity(value)
    && hasOptionalNodeText(value)
    && hasOptionalNodeMetrics(value)
    && hasOptionalNodeRelations(value);
}

function hasNodeIdentity(value: Record<string, unknown>): boolean {
  return typeof value.id === "string" && isNumber(value.community);
}

function hasOptionalNodeText(value: Record<string, unknown>): boolean {
  return optionalString(value.label)
    && optionalString(value.lemma)
    && optionalString(value.title)
    && optionalString(value.slug)
    && optionalStringArray(value.tags);
}

function hasOptionalNodeMetrics(value: Record<string, unknown>): boolean {
  return optionalNumber(value.number)
    && optionalNumber(value.frequency)
    && optionalNumber(value.centrality)
    && optionalNumber(value.degree);
}

function hasOptionalNodeRelations(value: Record<string, unknown>): boolean {
  return optionalArray(value.top_concepts, isTopConcept)
    && optionalArray(value.top_books, isTopBook)
    && optionalArray(value.top_similar, isSimilarRef)
    && optionalArray(value.top_similar_embed, isSimilarRef);
}

function isEdge(value: unknown): value is PayloadEdge {
  return isRecord(value)
    && typeof value.source === "string"
    && typeof value.target === "string"
    && isNumber(value.weight)
    && optionalNumber(value.npmi)
    && optionalArray(value.shared_concepts, isTopConcept);
}

function isTopConcept(value: unknown): value is TopConceptRef {
  return isRecord(value)
    && optionalString(value.concept_id)
    && optionalString(value.label)
    && optionalString(value.lemma)
    && optionalNumber(value.count)
    && optionalNumber(value.score)
    && optionalNumber(value.coverage)
    && optionalNumber(value.weight)
    && optionalBoolean(value.untranslated);
}

function isTopBook(value: unknown): value is { slug: string; kind?: "book"; title: string; count?: number } {
  return isRecord(value)
    && typeof value.slug === "string"
    && (value.kind === undefined || value.kind === "book")
    && typeof value.title === "string"
    && optionalNumber(value.count);
}

function isSimilarRef(value: unknown): value is SimilarRef {
  return isRecord(value)
    && typeof value.slug === "string"
    && (value.kind === "book" || value.kind === "poem" || value.kind === "project")
    && typeof value.title === "string"
    && optionalNumber(value.weight)
    && optionalArray(value.shared_concepts, isTopConcept);
}

function optionalArray<T>(value: unknown, guard: (item: unknown) => item is T): boolean {
  return value === undefined || (Array.isArray(value) && value.every(guard));
}

function optionalStringArray(value: unknown): boolean {
  return value === undefined || (Array.isArray(value) && value.every((item) => typeof item === "string"));
}

function optionalString(value: unknown): boolean {
  return value === undefined || typeof value === "string";
}

function optionalNumber(value: unknown): boolean {
  return value === undefined || isNumber(value);
}

function optionalBoolean(value: unknown): boolean {
  return value === undefined || typeof value === "boolean";
}

function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
