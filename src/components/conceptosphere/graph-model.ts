import * as graphology from "graphology";
import type { AbstractGraph, GraphConstructor } from "graphology-types";
import type Sigma from "sigma";

import type { CommunityCatalog, GraphData, GraphEdgeData, GraphNodeData } from "./graph-data.ts";
import { layoutGraphGeometry } from "./graph-layout.ts";
import { edgeVisual, type GraphTheme } from "./graph-theme.ts";
import type { SimilarRef, TopBookRef, TopConceptRef } from "./graph-types.ts";

export interface ConceptNodeAttributes {
  label: string;
  title?: string;
  bookNumberBadge?: string;
  x: number;
  y: number;
  size: number;
  baseSize: number;
  labelSize: number;
  color: string;
  baseColor: string;
  communityId: number;
  frequency?: number;
  centrality?: number;
  tags: string[];
  topBooks: TopBookRef[];
  topConcepts: TopConceptRef[];
  similarByConcepts: SimilarRef[];
  similarByMeaning: SimilarRef[];
  number?: number;
  slug?: string;
  forceLabel: boolean;
}

export interface ConceptEdgeAttributes {
  size: number;
  baseSize: number;
  color: string;
  baseColor: string;
  tone: number;
  weight: number;
  withinCommunity: boolean;
  visibleBackbone: boolean;
  hidden: boolean;
}

export type ConceptGraph = AbstractGraph<ConceptNodeAttributes, ConceptEdgeAttributes>;
export type ConceptRenderer = Sigma<ConceptNodeAttributes, ConceptEdgeAttributes>;

export interface GraphModel {
  graph: ConceptGraph;
  communities: CommunityCatalog;
}

export function buildGraphModel(
  data: GraphData,
  theme: GraphTheme,
  reduceMotion: boolean,
): GraphModel {
  const Graph = (graphology as unknown as {
    default: GraphConstructor<ConceptNodeAttributes, ConceptEdgeAttributes>;
  }).default;
  const graph: ConceptGraph = new Graph({ type: "undirected", multi: false });
  const sizedNodes = data.nodes.map((node) => ({ node, size: nodeSize(node, data.mode) }));
  const maxSize = sizedNodes.reduce((max, { size }) => Math.max(max, size), 0);
  const forcedLabelThreshold = maxSize * 0.62;

  for (const { node, size } of sizedNodes) {
    const community = requireCommunity(data, node.communityId);
    const attrs: ConceptNodeAttributes = {
      label: node.label,
      x: 0,
      y: 0,
      size,
      baseSize: size,
      labelSize: labelSizeFor(size, maxSize),
      color: community.color,
      baseColor: community.color,
      communityId: node.communityId,
      tags: node.tags,
      topBooks: node.relations.topBooks,
      topConcepts: node.relations.topConcepts,
      similarByConcepts: node.relations.similarByConcepts,
      similarByMeaning: node.relations.similarByMeaning,
      forceLabel: data.mode === "books" ? false : size >= forcedLabelThreshold,
      ...(node.title !== undefined ? { title: node.title } : {}),
      ...(node.bookNumberBadge !== undefined ? { bookNumberBadge: node.bookNumberBadge } : {}),
      ...(node.metrics.frequency !== undefined ? { frequency: node.metrics.frequency } : {}),
      ...(node.metrics.centrality !== undefined ? { centrality: node.metrics.centrality } : {}),
      ...(node.number !== undefined ? { number: node.number } : {}),
      ...(node.slug !== undefined ? { slug: node.slug } : {}),
    };
    graph.addNode(node.id, attrs);
  }

  forceAnchorLabels(graph, data);
  addGraphEdges({ graph, data, theme });
  layoutGraphGeometry({ graph, data, reduceMotion });

  return { graph, communities: data.communities };
}

function nodeSize(node: GraphNodeData, mode: GraphData["mode"]): number {
  const cent = Math.max(0, node.metrics.centrality ?? 0);
  const freq = (node.metrics.frequency ?? 1) + 1;
  if (mode === "books") {
    return Math.max(11, Math.min(32, 9 + Math.sqrt(cent) * 90 + Math.log10(freq) * 1.6));
  }
  return Math.max(2.4, Math.min(32, Math.sqrt(cent) * 130 + Math.log10(freq) * 0.9));
}

function labelSizeFor(size: number, maxSize: number): number {
  if (size >= maxSize * 0.78) return 18;
  if (size >= maxSize * 0.55) return 15.5;
  if (size >= maxSize * 0.36) return 13.5;
  return 12.5;
}

function forceAnchorLabels(graph: ConceptGraph, data: GraphData): void {
  if (data.mode !== "concepts") return;

  for (const nodes of data.communities.nodesByCommunity.values()) {
    const labelCount = nodes.length >= 25 ? 2 : 1;
    for (const node of nodes.slice(0, labelCount)) {
      if (graph.hasNode(node.id)) graph.setNodeAttribute(node.id, "forceLabel", true);
    }
  }
}

function addGraphEdges(input: {
  graph: ConceptGraph;
  data: GraphData;
  theme: GraphTheme;
}): void {
  const { graph, data, theme } = input;
  const weights = data.edges.map((edge) => edge.weight);
  const minWeight = weights.length ? Math.min(...weights) : 0;
  const maxWeight = weights.length ? Math.max(...weights) : 0;
  const weightRange = Math.max(maxWeight - minWeight, 1e-9);
  const visibleCrossEdges = visibleCrossEdgeKeys(graph, data);

  for (const edge of data.edges) {
    if (!graph.hasNode(edge.source) || !graph.hasNode(edge.target)) continue;
    if (graph.hasEdge(edge.source, edge.target)) continue;

    const sourceCommunityId = graph.getNodeAttribute(edge.source, "communityId");
    const targetCommunityId = graph.getNodeAttribute(edge.target, "communityId");
    const within = sourceCommunityId === targetCommunityId;
    const isBackbone = within || visibleCrossEdges.has(edgeKey(edge));
    const tone = Math.max(0, (edge.weight - minWeight) / weightRange) ** 0.55;
    const sourceCommunity = requireCommunity(data, sourceCommunityId);
    const targetCommunity = requireCommunity(data, targetCommunityId);
    const visual = edgeVisual(theme, data.mode, within, tone, sourceCommunity.rgb, targetCommunity.rgb);

    graph.addEdge(edge.source, edge.target, {
      size: visual.size,
      baseSize: visual.size,
      color: visual.color,
      baseColor: visual.color,
      tone,
      weight: edge.weight,
      withinCommunity: within,
      visibleBackbone: isBackbone,
      hidden: !isBackbone,
    });
  }
}

function visibleCrossEdgeKeys(graph: ConceptGraph, data: GraphData): Set<string> {
  const keep = data.mode === "books" ? 6 : 3;
  const crossByPair = new Map<string, GraphEdgeData[]>();

  for (const edge of data.edges) {
    if (!graph.hasNode(edge.source) || !graph.hasNode(edge.target)) continue;
    const sourceCommunityId = graph.getNodeAttribute(edge.source, "communityId");
    const targetCommunityId = graph.getNodeAttribute(edge.target, "communityId");
    if (sourceCommunityId === targetCommunityId) continue;

    const pairKey = `${Math.min(sourceCommunityId, targetCommunityId)}|${Math.max(sourceCommunityId, targetCommunityId)}`;
    const bucket = crossByPair.get(pairKey) ?? [];
    bucket.push(edge);
    crossByPair.set(pairKey, bucket);
  }

  const visible = new Set<string>();
  for (const bucket of crossByPair.values()) {
    bucket.sort((a, b) => (b.npmi ?? b.weight) - (a.npmi ?? a.weight));
    for (const edge of bucket.slice(0, keep)) visible.add(edgeKey(edge));
  }
  return visible;
}

function edgeKey(edge: GraphEdgeData): string {
  return `${edge.source}|${edge.target}`;
}

function requireCommunity(data: GraphData, communityId: number) {
  const community = data.communities.byId.get(communityId);
  if (!community) throw new Error(`conceptosphere graph model missing community ${communityId}`);
  return community;
}
