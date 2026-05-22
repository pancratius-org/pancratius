import type { Community, EdgeJson, GraphPayload, NodeJson, SimilarRef } from "./runtime-types";

export function parseGraphPayload(value: unknown, url: string): GraphPayload {
  if (!isRecord(value) || !Array.isArray(value.communities) || !Array.isArray(value.nodes) || !Array.isArray(value.edges)) {
    throw new Error(`Invalid graph payload: ${url}`);
  }

  value.communities.forEach((community, index) => {
    if (!isCommunity(community)) {
      throw new Error(`Invalid graph community[${index}] in ${url}`);
    }
  });
  value.nodes.forEach((node, index) => {
    if (!isNode(node)) {
      throw new Error(`Invalid graph node[${index}] in ${url}`);
    }
  });
  value.edges.forEach((edge, index) => {
    if (!isEdge(edge)) {
      throw new Error(`Invalid graph edge[${index}] in ${url}`);
    }
  });

  return {
    stats: isRecord(value.stats)
      ? value.stats as Record<string, number | string | undefined>
      : {},
    communities: value.communities as Community[],
    nodes: value.nodes as NodeJson[],
    edges: value.edges as EdgeJson[],
  };
}

function isCommunity(value: unknown): value is Community {
  return isRecord(value)
    && isNumber(value.id)
    && typeof value.label === "string"
    && isNumber(value.size);
}

function isNode(value: unknown): value is NodeJson {
  if (!isRecord(value) || typeof value.id !== "string" || !isNumber(value.community)) return false;
  return optionalString(value.label)
    && optionalString(value.lemma)
    && optionalString(value.title)
    && optionalString(value.slug)
    && optionalNumber(value.number)
    && optionalStringArray(value.tags)
    && optionalNumber(value.frequency)
    && optionalNumber(value.centrality)
    && optionalNumber(value.degree)
    && optionalArray(value.top_concepts, isTopConcept)
    && optionalArray(value.top_books, isTopBook)
    && optionalArray(value.top_similar, isSimilarRef)
    && optionalArray(value.top_similar_embed, isSimilarRef);
}

function isEdge(value: unknown): value is EdgeJson {
  return isRecord(value)
    && typeof value.source === "string"
    && typeof value.target === "string"
    && isNumber(value.weight)
    && optionalNumber(value.npmi);
}

function isTopConcept(value: unknown): value is { label?: string; lemma?: string; count?: number } {
  return isRecord(value)
    && optionalString(value.label)
    && optionalString(value.lemma)
    && optionalNumber(value.count);
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
    && optionalNumber(value.weight);
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

function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
