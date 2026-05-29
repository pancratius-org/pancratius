import * as graphology from "graphology";
import * as forceAtlas2 from "graphology-layout-forceatlas2";
import type { AbstractGraph, GraphConstructor } from "graphology-types";
import type { ForceAtlas2Settings } from "graphology-layout-forceatlas2";

import type { GraphData, GraphNodeData } from "./graph-data.ts";
import { GRAPH_GEOMETRY_PROFILE } from "./graph-geometry-config.ts";
import type { ConceptGraph } from "./graph-model.ts";

interface LayoutRequest {
  graph: ConceptGraph;
  data: GraphData;
  reduceMotion: boolean;
}

interface LayoutNodeAttributes {
  x: number;
  y: number;
  size: number;
}

interface WeightedEdgeAttributes {
  weight: number;
}

type LayoutGraph = AbstractGraph<LayoutNodeAttributes, WeightedEdgeAttributes>;
type CollisionId = string | number;

interface Point {
  x: number;
  y: number;
}

interface Circle extends Point {
  r: number;
}

interface CollisionBody<Id extends CollisionId> extends Circle {
  id: Id;
}

interface RelaxCollisionOptions<Id extends CollisionId> {
  iterations: number;
  ratio: number;
  strength: number;
  settleThreshold: number;
  damping: (iteration: number) => number;
  jitter: (a: CollisionBody<Id>, b: CollisionBody<Id>, i: number, j: number) => number;
}

const Graph = (graphology as unknown as {
  default: GraphConstructor<LayoutNodeAttributes, WeightedEdgeAttributes>;
}).default;
const forceAtlas = (forceAtlas2 as unknown as {
  default: {
    inferSettings(graph: LayoutGraph): ForceAtlas2Settings;
    assign(graph: LayoutGraph, params: { iterations: number; settings: ForceAtlas2Settings }): void;
  };
}).default;

export function layoutGraphGeometry(input: LayoutRequest): void {
  const { graph, data, reduceMotion } = input;
  const profile = GRAPH_GEOMETRY_PROFILE[data.mode];
  const communityPositions = placeCommunities(graph, data);
  placeNodesWithinCommunities(graph, data, communityPositions, reduceMotion);

  relaxOverlaps(graph, {
    iterations: reduceMotion ? profile.nodeOverlap.reducedMotionIterations : profile.nodeOverlap.defaultIterations,
    margin: profile.nodeOverlap.marginGraphUnits,
    ratio: profile.nodeOverlap.distanceRatio,
  });

  if (profile.communityOverlap) {
    relaxCommunityCircles(graph, data.communities.nodesByCommunity, {
      iterations: reduceMotion
        ? profile.communityOverlap.reducedMotionIterations
        : profile.communityOverlap.defaultIterations,
      margin: profile.communityOverlap.marginGraphUnits,
      ratio: profile.communityOverlap.distanceRatio,
      strength: profile.communityOverlap.strength,
    });
  }

  recenterGraph(graph);
}

function placeCommunities(
  graph: ConceptGraph,
  data: GraphData,
): Map<number, { x: number; y: number; angle: number }> {
  const communityGraph = buildCommunityLayoutGraph(data, communityTies(graph, data));
  runCommunityForceLayout(communityGraph);
  return communityRingPositions(data, orderCommunitiesByLayoutAngle(communityGraph));
}

function communityTies(
  graph: ConceptGraph,
  data: GraphData,
): Map<number, Map<number, number>> {
  const ties = new Map<number, Map<number, number>>();
  for (const community of data.communities.all) ties.set(community.id, new Map());

  graph.forEachEdge((_edge, attrs, _source, _target, sourceAttrs, targetAttrs) => {
    addCommunityTie(ties, sourceAttrs.communityId, targetAttrs.communityId, attrs.weight);
  });
  return ties;
}

function addCommunityTie(
  ties: Map<number, Map<number, number>>,
  sourceCommunityId: number,
  targetCommunityId: number,
  weight: number,
): void {
  if (sourceCommunityId === targetCommunityId) return;
  incrementTie(requireMapEntry(ties, sourceCommunityId, "community ties"), targetCommunityId, weight);
  incrementTie(requireMapEntry(ties, targetCommunityId, "community ties"), sourceCommunityId, weight);
}

function incrementTie(ties: Map<number, number>, communityId: number, weight: number): void {
  ties.set(communityId, (ties.get(communityId) ?? 0) + weight);
}

function buildCommunityLayoutGraph(
  data: GraphData,
  tiesByCommunity: ReadonlyMap<number, ReadonlyMap<number, number>>,
): LayoutGraph {
  const communityGraph: LayoutGraph = new Graph({ type: "undirected", multi: false });
  addCommunityLayoutNodes(communityGraph, data);
  addCommunityLayoutEdges(communityGraph, tiesByCommunity);
  return communityGraph;
}

function addCommunityLayoutNodes(communityGraph: LayoutGraph, data: GraphData): void {
  const communityCount = Math.max(1, data.communities.all.length);
  for (const community of data.communities.all) {
    communityGraph.addNode(String(community.id), communitySeedPosition(community.id, community.size, communityCount));
  }
}

function communitySeedPosition(id: number, size: number, communityCount: number): LayoutNodeAttributes {
  const angle = (id / communityCount) * Math.PI * 2;
  return {
    x: Math.cos(angle) * 10,
    y: Math.sin(angle) * 10,
    size: Math.sqrt(size) * 4,
  };
}

function addCommunityLayoutEdges(
  communityGraph: LayoutGraph,
  tiesByCommunity: ReadonlyMap<number, ReadonlyMap<number, number>>,
): void {
  for (const [communityId, ties] of tiesByCommunity) {
    for (const [otherCommunityId, weight] of ties) {
      addCommunityLayoutEdge(communityGraph, communityId, otherCommunityId, weight);
    }
  }
}

function addCommunityLayoutEdge(
  communityGraph: LayoutGraph,
  communityId: number,
  otherCommunityId: number,
  weight: number,
): void {
  const sourceId = String(communityId);
  const targetId = String(otherCommunityId);
  if (communityId < otherCommunityId && communityGraph.hasNode(sourceId) && communityGraph.hasNode(targetId)) {
    communityGraph.addEdge(sourceId, targetId, { weight });
  }
}

function runCommunityForceLayout(communityGraph: LayoutGraph): void {
  if (communityGraph.order < 2 || communityGraph.size < 1) return;

  const settings = forceAtlas.inferSettings(communityGraph);
  settings.gravity = 1.5;
  settings.scalingRatio = 40;
  settings.slowDown = 4;
  settings.strongGravityMode = true;
  settings.barnesHutOptimize = false;
  settings.linLogMode = false;
  settings.adjustSizes = true;
  settings.edgeWeightInfluence = 1.2;
  forceAtlas.assign(communityGraph, { iterations: 300, settings });
}

function orderCommunitiesByLayoutAngle(communityGraph: LayoutGraph): { id: number; angle: number }[] {
  const communityAngles: { id: number; angle: number }[] = [];
  communityGraph.forEachNode((id, attrs) => {
    communityAngles.push({ id: Number(id), angle: Math.atan2(attrs.y, attrs.x) });
  });
  return communityAngles.sort((a, b) => a.angle - b.angle);
}

function communityRingPositions(
  data: GraphData,
  orderedCommunities: readonly { id: number; angle: number }[],
): Map<number, { x: number; y: number; angle: number }> {
  const placement = GRAPH_GEOMETRY_PROFILE[data.mode].communityPlacement;
  const positions = new Map<number, { x: number; y: number; angle: number }>();
  const assignedAngles = weightedCommunityAngles(data, orderedCommunities);

  orderedCommunities.forEach((community, index) => {
    const angle = requireMapEntry(assignedAngles, community.id, "community angle");
    const size = communitySize(data, community.id);
    const ringRadius = cycleAt(placement.ringTiersGraphUnits, index)
      + Math.sqrt(size / placement.ringGrowthSizeDivisor) * placement.ringGrowthGraphUnits;
    positions.set(community.id, polarPoint(angle, ringRadius));
  });
  return positions;
}

function weightedCommunityAngles(
  data: GraphData,
  orderedCommunities: readonly { id: number }[],
): Map<number, number> {
  const totalWeight = orderedCommunities.reduce((sum, community) =>
    sum + Math.sqrt(communitySize(data, community.id)), 0) || 1;
  const assignedAngles = new Map<number, number>();
  let cursor = 0;

  for (const community of orderedCommunities) {
    const span = (Math.sqrt(communitySize(data, community.id)) / totalWeight) * Math.PI * 2;
    assignedAngles.set(community.id, cursor + span / 2);
    cursor += span;
  }
  return assignedAngles;
}

function polarPoint(angle: number, radius: number): { x: number; y: number; angle: number } {
  return {
    x: Math.cos(angle) * radius,
    y: Math.sin(angle) * radius,
    angle,
  };
}

function placeNodesWithinCommunities(
  graph: ConceptGraph,
  data: GraphData,
  communityPositions: ReadonlyMap<number, { x: number; y: number; angle: number }>,
  reduceMotion: boolean,
): void {
  for (const [communityId, nodes] of data.communities.nodesByCommunity) {
    const localGraph: LayoutGraph =
      new Graph({ type: "undirected", multi: false });

    nodes.forEach((node, index) => {
      if (!graph.hasNode(node.id)) return;
      const radius = 1 + (index / Math.max(1, nodes.length - 1)) * 3.5;
      const angle = (index / Math.max(1, nodes.length)) * Math.PI * 2;
      localGraph.addNode(node.id, {
        x: Math.cos(angle) * radius,
        y: Math.sin(angle) * radius,
        size: graph.getNodeAttribute(node.id, "size"),
      });
    });

    graph.forEachEdge((_edge, attrs, source, target, sourceAttrs, targetAttrs) => {
      if (
        sourceAttrs.communityId === communityId
        && targetAttrs.communityId === communityId
        && localGraph.hasNode(source)
        && localGraph.hasNode(target)
      ) {
        localGraph.addEdge(source, target, { weight: attrs.weight });
      }
    });

    if (localGraph.order > 2) {
      const settings = forceAtlas.inferSettings(localGraph);
      settings.gravity = 1.2;
      settings.scalingRatio = data.mode === "books" ? 12 : 7;
      settings.slowDown = 6;
      settings.strongGravityMode = true;
      settings.adjustSizes = true;
      settings.linLogMode = true;
      settings.edgeWeightInfluence = 1.4;
      settings.barnesHutOptimize = false;
      forceAtlas.assign(localGraph, { iterations: reduceMotion ? 120 : 300, settings });
    }

    const centroid = communityPositions.get(communityId) ?? { x: 0, y: 0, angle: 0 };
    let localRadius = 0;
    localGraph.forEachNode((_id, attrs) => {
      localRadius = Math.max(localRadius, Math.hypot(attrs.x, attrs.y));
    });
    const targetRadius = communityRadius(data.mode, nodes.length);
    const localScale = localRadius > 0 ? targetRadius / localRadius : 1;

    localGraph.forEachNode((id, attrs) => {
      if (!graph.hasNode(id)) return;
      graph.setNodeAttribute(id, "x", attrs.x * localScale + centroid.x);
      graph.setNodeAttribute(id, "y", attrs.y * localScale + centroid.y);
    });
  }
}

function communityRadius(mode: GraphData["mode"], nodeCount: number): number {
  const profile = GRAPH_GEOMETRY_PROFILE[mode].communityLayout;
  return Math.min(
    profile.maxRadiusGraphUnits,
    profile.baseRadiusGraphUnits + Math.sqrt(nodeCount) * profile.radiusPerSqrtNodeGraphUnits,
  );
}

function relaxOverlaps(
  graph: ConceptGraph,
  options: { iterations: number; margin: number; ratio: number },
): void {
  relaxCollisionBodies(graph.nodes(), {
    iterations: options.iterations,
    ratio: options.ratio,
    strength: 1,
    settleThreshold: 0.015,
    damping: nodeOverlapDamping,
    jitter: (_a, _b, i, j) => prng(i * 4099 + j),
  }, {
    body: (nodeId) => nodeCollisionCircle(graph, nodeId, options.margin),
    apply: (displacements) => applyNodeDisplacements(graph, displacements),
  });
}

function relaxCommunityCircles(
  graph: ConceptGraph,
  nodesByCommunity: ReadonlyMap<number, readonly GraphNodeData[]>,
  options: { iterations: number; margin: number; ratio: number; strength: number },
): void {
  relaxCollisionBodies([...nodesByCommunity.keys()], {
    iterations: options.iterations,
    ratio: options.ratio,
    strength: options.strength,
    settleThreshold: 0.025,
    damping: () => 1,
    jitter: (a, b) => prng(a.id * 8191 + b.id),
  }, {
    body: (communityId) => communityCollisionCircle(graph, communityId, nodesByCommunity, options.margin),
    apply: (displacements) => applyCommunityDisplacements(graph, nodesByCommunity, displacements),
  });
}

function relaxCollisionBodies<Id extends CollisionId>(
  ids: readonly Id[],
  options: RelaxCollisionOptions<Id>,
  geometry: {
    body: (id: Id) => CollisionBody<Id> | null;
    apply: (displacements: ReadonlyMap<Id, Point>) => void;
  },
): void {
  if (ids.length < 2) return;

  for (let iteration = 0; iteration < options.iterations; iteration++) {
    const displacements = new Map<Id, Point>();
    const bodies = collisionBodies(ids, geometry.body);
    const moved = separateCollidingPairs(bodies, displacements, options);

    geometry.apply(scaleDisplacements(displacements, options.damping(iteration)));
    if (moved < options.settleThreshold) break;
  }
}

function collisionBodies<Id extends CollisionId>(
  ids: readonly Id[],
  readBody: (id: Id) => CollisionBody<Id> | null,
): CollisionBody<Id>[] {
  const bodies: CollisionBody<Id>[] = [];
  for (const id of ids) {
    const body = readBody(id);
    if (body) bodies.push(body);
  }
  return bodies;
}

function separateCollidingPairs<Id extends CollisionId>(
  bodies: readonly CollisionBody<Id>[],
  displacements: Map<Id, Point>,
  options: RelaxCollisionOptions<Id>,
): number {
  let moved = 0;
  for (const [i, source] of bodies.entries()) {
    let j = i + 1;
    for (const target of bodies.slice(i + 1)) {
      const push = overlapPush(source, target, options, i, j);
      if (push) applyPairPush(displacements, source.id, target.id, push);
      moved = Math.max(moved, push?.amount ?? 0);
      j++;
    }
  }
  return moved;
}

function overlapPush<Id extends CollisionId>(
  source: CollisionBody<Id>,
  target: CollisionBody<Id>,
  options: RelaxCollisionOptions<Id>,
  sourceIndex: number,
  targetIndex: number,
): Point & { amount: number } | null {
  const axis = separationAxis(source, target, options.jitter(source, target, sourceIndex, targetIndex));
  const minimumDistance = (source.r + target.r) * options.ratio;
  if (axis.distance >= minimumDistance) return null;

  const amount = (minimumDistance - axis.distance) * 0.5 * options.strength;
  return {
    x: axis.unitX * amount,
    y: axis.unitY * amount,
    amount,
  };
}

function separationAxis(source: Circle, target: Circle, jitter: number): {
  unitX: number;
  unitY: number;
  distance: number;
} {
  const vector = nonZeroVector({ x: target.x - source.x, y: target.y - source.y }, jitter);
  return {
    unitX: vector.x / vector.distance,
    unitY: vector.y / vector.distance,
    distance: vector.distance,
  };
}

function nonZeroVector(vector: Point, jitter: number): Point & { distance: number } {
  const distance = Math.hypot(vector.x, vector.y);
  if (distance >= 0.001) return { ...vector, distance };

  const angle = jitter * Math.PI * 2;
  return {
    x: Math.cos(angle) * 0.001,
    y: Math.sin(angle) * 0.001,
    distance: 0.001,
  };
}

function applyPairPush<Id extends CollisionId>(
  displacements: Map<Id, Point>,
  sourceId: Id,
  targetId: Id,
  push: Point,
): void {
  addDisplacement(displacements, sourceId, -push.x, -push.y);
  addDisplacement(displacements, targetId, push.x, push.y);
}

function addDisplacement<Id extends CollisionId>(
  displacements: Map<Id, Point>,
  id: Id,
  x: number,
  y: number,
): void {
  const current = displacements.get(id) ?? { x: 0, y: 0 };
  displacements.set(id, { x: current.x + x, y: current.y + y });
}

function scaleDisplacements<Id extends CollisionId>(
  displacements: ReadonlyMap<Id, Point>,
  scale: number,
): Map<Id, Point> {
  const scaled = new Map<Id, Point>();
  for (const [id, point] of displacements) scaled.set(id, { x: point.x * scale, y: point.y * scale });
  return scaled;
}

function nodeCollisionCircle(
  graph: ConceptGraph,
  nodeId: string,
  margin: number,
): CollisionBody<string> {
  const attrs = graph.getNodeAttributes(nodeId);
  return {
    id: nodeId,
    x: attrs.x,
    y: attrs.y,
    r: attrs.baseSize + margin,
  };
}

function applyNodeDisplacements(
  graph: ConceptGraph,
  displacements: ReadonlyMap<string, Point>,
): void {
  for (const [nodeId, displacement] of displacements) {
    graph.updateNodeAttribute(nodeId, "x", (value) => (value ?? 0) + displacement.x);
    graph.updateNodeAttribute(nodeId, "y", (value) => (value ?? 0) + displacement.y);
  }
}

function communityCollisionCircle(
  graph: ConceptGraph,
  communityId: number,
  nodesByCommunity: ReadonlyMap<number, readonly GraphNodeData[]>,
  margin: number,
): CollisionBody<number> | null {
  const nodes = nodesByCommunity.get(communityId) ?? [];
  const center = communityCenter(graph, nodes);
  if (!center) return null;

  return {
    id: communityId,
    x: center.x,
    y: center.y,
    r: communityCircleRadius(graph, nodes, center) + margin,
  };
}

function communityCenter(graph: ConceptGraph, nodes: readonly GraphNodeData[]): Point | null {
  let center = { x: 0, y: 0 };
  let count = 0;
  for (const node of nodes) {
    if (!graph.hasNode(node.id)) continue;
    const attrs = graph.getNodeAttributes(node.id);
    center = { x: center.x + attrs.x, y: center.y + attrs.y };
    count++;
  }
  return count ? { x: center.x / count, y: center.y / count } : null;
}

function communityCircleRadius(
  graph: ConceptGraph,
  nodes: readonly GraphNodeData[],
  center: Point,
): number {
  let radius = 0;
  for (const node of nodes) {
    if (!graph.hasNode(node.id)) continue;
    const attrs = graph.getNodeAttributes(node.id);
    const nodeRadius = attrs.baseSize * 0.72;
    radius = Math.max(radius, Math.hypot(attrs.x - center.x, attrs.y - center.y) + nodeRadius);
  }
  return radius;
}

function applyCommunityDisplacements(
  graph: ConceptGraph,
  nodesByCommunity: ReadonlyMap<number, readonly GraphNodeData[]>,
  displacements: ReadonlyMap<number, Point>,
): void {
  for (const [communityId, displacement] of displacements) {
    shiftCommunityNodes(graph, nodesByCommunity.get(communityId) ?? [], displacement);
  }
}

function shiftCommunityNodes(
  graph: ConceptGraph,
  nodes: readonly GraphNodeData[],
  displacement: Point,
): void {
  for (const node of nodes) {
    if (!graph.hasNode(node.id)) continue;
    graph.updateNodeAttribute(node.id, "x", (value) => (value ?? 0) + displacement.x);
    graph.updateNodeAttribute(node.id, "y", (value) => (value ?? 0) + displacement.y);
  }
}

function nodeOverlapDamping(iteration: number): number {
  return iteration < 20 ? 0.55 : 0.38;
}

function recenterGraph(graph: ConceptGraph): void {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;

  graph.forEachNode((_node, attrs) => {
    if (attrs.x < minX) minX = attrs.x;
    if (attrs.y < minY) minY = attrs.y;
    if (attrs.x > maxX) maxX = attrs.x;
    if (attrs.y > maxY) maxY = attrs.y;
  });

  const centerX = (minX + maxX) / 2;
  const centerY = (minY + maxY) / 2;
  graph.forEachNode((id, attrs) => {
    graph.setNodeAttribute(id, "x", attrs.x - centerX);
    graph.setNodeAttribute(id, "y", attrs.y - centerY);
  });
}

function communitySize(data: GraphData, communityId: number): number {
  return data.communities.byId.get(communityId)?.size ?? 0;
}

function requireMapEntry<K, V>(map: ReadonlyMap<K, V>, key: K, label: string): V {
  const value = map.get(key);
  if (value === undefined) throw new Error(`conceptosphere graph layout missing ${label}: ${String(key)}`);
  return value;
}

function cycleAt<T>(items: readonly [T, ...T[]], index: number): T {
  const item = items[index % items.length];
  if (item === undefined) throw new Error("cycleAt received an empty cycle");
  return item;
}

function prng(index: number): number {
  const x = Math.sin(index * 12.9898 + 78.233) * 43758.5453;
  return x - Math.floor(x);
}
