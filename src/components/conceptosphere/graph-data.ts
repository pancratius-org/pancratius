import { parseGraphPayload, type GraphPayload, type PayloadEdge, type PayloadNode } from "./graph-payload.ts";
import type { ConceptosphereMode, SimilarRef, TopBookRef, TopConceptRef } from "./graph-types.ts";
import { communityColor, communityRgb } from "./palette.ts";

export interface GraphCommunity {
  id: number;
  label: string;
  size: number;
  color: string;
  rgb: [number, number, number];
}

interface GraphNodeMetrics {
  frequency?: number;
  centrality?: number;
}

interface GraphNodeRelations {
  topBooks: TopBookRef[];
  topConcepts: TopConceptRef[];
  similarByConcepts: SimilarRef[];
  similarByMeaning: SimilarRef[];
}

export interface GraphNodeData {
  id: string;
  communityId: number;
  label: string;
  title?: string;
  bookNumberBadge?: string;
  number?: number;
  slug?: string;
  tags: string[];
  metrics: GraphNodeMetrics;
  relations: GraphNodeRelations;
}

export interface GraphEdgeData {
  source: string;
  target: string;
  weight: number;
  npmi?: number;
}

export interface CommunityCatalog {
  all: readonly GraphCommunity[];
  sortedBySize: readonly GraphCommunity[];
  byId: ReadonlyMap<number, GraphCommunity>;
  nodesByCommunity: ReadonlyMap<number, readonly GraphNodeData[]>;
}

export interface GraphData {
  mode: ConceptosphereMode;
  stats: Record<string, number | string | undefined>;
  communities: CommunityCatalog;
  nodes: readonly GraphNodeData[];
  edges: readonly GraphEdgeData[];
}

export interface GraphDataConfig {
  conceptsUrl: string;
  booksUrl: string;
  bookSlugInfo: Record<string, { title: string }>;
}

export class GraphDataStore {
  private readonly cache = new Map<ConceptosphereMode, GraphData>();
  private readonly cfg: GraphDataConfig;

  constructor(cfg: GraphDataConfig) {
    this.cfg = cfg;
  }

  async load(mode: ConceptosphereMode): Promise<GraphData> {
    const cached = this.cache.get(mode);
    if (cached) return cached;

    const url = mode === "books" ? this.cfg.booksUrl : this.cfg.conceptsUrl;
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${url} (${response.status})`);

    const payload = parseGraphPayload(await response.json() as unknown, url);
    const data = normalizeGraphPayload(payload, mode, this.cfg);
    this.cache.set(mode, data);
    return data;
  }
}

function normalizeGraphPayload(
  payload: GraphPayload,
  mode: ConceptosphereMode,
  cfg: GraphDataConfig,
): GraphData {
  const communities = payload.communities.map((community): GraphCommunity => ({
    id: community.id,
    label: community.label,
    size: community.size,
    color: communityColor(community.id),
    rgb: communityRgb(community.id),
  }));
  const communityById = new Map(communities.map((community) => [community.id, community]));
  const nodes = payload.nodes.map((node) => normalizeNode(node, mode, cfg, communityById));
  const edges = payload.edges.map(normalizeEdge);
  const nodesByCommunity = groupNodesByCommunity(communities, nodes);

  return {
    mode,
    stats: { ...payload.stats },
    communities: {
      all: communities,
      sortedBySize: [...communities].sort((a, b) => b.size - a.size),
      byId: communityById,
      nodesByCommunity,
    },
    nodes,
    edges,
  };
}

function normalizeNode(
  node: PayloadNode,
  mode: ConceptosphereMode,
  cfg: GraphDataConfig,
  communityById: ReadonlyMap<number, GraphCommunity>,
): GraphNodeData {
  assertKnownCommunity(node, communityById);
  return mode === "books"
    ? normalizeBookNode(node, cfg)
    : normalizeConceptNode(node, cfg);
}

function normalizeBookNode(node: PayloadNode, cfg: GraphDataConfig): GraphNodeData {
  const localizedTitle = localizedBookTitle(cfg, node.slug ?? node.id);
  const title = localizedTitle ?? node.title ?? node.label;
  return baseNodeData(node, {
    label: title ?? node.id,
    title,
    relations: localizedNodeRelations(node, cfg),
    bookNumberBadge: node.number === undefined ? undefined : String(node.number),
  });
}

function normalizeConceptNode(node: PayloadNode, cfg: GraphDataConfig): GraphNodeData {
  return baseNodeData(node, {
    label: node.label ?? node.id,
    title: node.title ?? node.label,
    relations: localizedNodeRelations(node, cfg),
  });
}

function baseNodeData(
  node: PayloadNode,
  view: {
    label: string;
    title: string | undefined;
    relations: GraphNodeRelations;
    bookNumberBadge?: string | undefined;
  },
): GraphNodeData {
  return {
    id: node.id,
    communityId: node.community,
    label: view.label,
    tags: [...(node.tags ?? [])],
    metrics: nodeMetrics(node),
    relations: view.relations,
    ...(view.title !== undefined ? { title: view.title } : {}),
    ...(view.bookNumberBadge !== undefined ? { bookNumberBadge: view.bookNumberBadge } : {}),
    ...(node.number !== undefined ? { number: node.number } : {}),
    ...(node.slug !== undefined ? { slug: node.slug } : {}),
  };
}

function localizedNodeRelations(node: PayloadNode, cfg: GraphDataConfig): GraphNodeRelations {
  return {
    topBooks: (node.top_books ?? []).map((book) => localizeTopBook(book, cfg)),
    topConcepts: (node.top_concepts ?? []).map((concept) => ({ ...concept })),
    similarByConcepts: (node.top_similar ?? []).map((ref) => localizeSimilarRef(ref, cfg)),
    similarByMeaning: (node.top_similar_embed ?? []).map((ref) => localizeSimilarRef(ref, cfg)),
  };
}

function assertKnownCommunity(
  node: PayloadNode,
  communityById: ReadonlyMap<number, GraphCommunity>,
): void {
  if (!communityById.has(node.community)) {
    throw new Error(`conceptosphere graph node ${JSON.stringify(node.id)} references unknown community ${node.community}`);
  }
}

function normalizeEdge(edge: PayloadEdge): GraphEdgeData {
  return {
    source: edge.source,
    target: edge.target,
    weight: edge.weight,
    ...(edge.npmi !== undefined ? { npmi: edge.npmi } : {}),
  };
}

function nodeMetrics(node: PayloadNode): GraphNodeMetrics {
  return {
    ...(node.frequency !== undefined ? { frequency: node.frequency } : {}),
    ...(node.centrality !== undefined ? { centrality: node.centrality } : {}),
  };
}

function localizeTopBook(book: TopBookRef, cfg: GraphDataConfig): TopBookRef {
  return {
    ...book,
    title: localizedBookTitle(cfg, book.slug) ?? book.title,
  };
}

function localizeSimilarRef(ref: SimilarRef, cfg: GraphDataConfig): SimilarRef {
  return {
    ...ref,
    title: ref.kind === "book" ? localizedBookTitle(cfg, ref.slug) ?? ref.title : ref.title,
  };
}

function localizedBookTitle(cfg: GraphDataConfig, slug: string | undefined): string | null {
  if (!slug) return null;
  return cfg.bookSlugInfo[slug]?.title ?? null;
}

function groupNodesByCommunity(
  communities: readonly GraphCommunity[],
  nodes: readonly GraphNodeData[],
): ReadonlyMap<number, readonly GraphNodeData[]> {
  const groups = new Map<number, GraphNodeData[]>();
  for (const community of communities) groups.set(community.id, []);

  for (const node of nodes) {
    const group = groups.get(node.communityId);
    if (!group) {
      throw new Error(`conceptosphere graph data missing community bucket ${node.communityId}`);
    }
    group.push(node);
  }

  for (const group of groups.values()) {
    group.sort((a, b) => (b.metrics.centrality ?? 0) - (a.metrics.centrality ?? 0));
  }
  return groups;
}
