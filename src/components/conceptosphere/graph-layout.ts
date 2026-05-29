import Graph from "graphology";
import forceAtlas2 from "graphology-layout-forceatlas2";

import { edgeVisual, type GraphTheme } from "./graph-theme";
import { communityColor, communityRgb } from "./palette";
import type { ConceptosphereMode, EdgeJson, GraphPayload, NodeJson } from "./runtime-types";

export interface GraphModel {
  graph: Graph;
  nodesByCom: Map<number, NodeJson[]>;
  comColor: Map<number, string>;
  comLabel: Map<number, string>;
  comSize: Map<number, number>;
  comRgb: Map<number, [number, number, number]>;
}

function nodeSize(n: NodeJson, mode: ConceptosphereMode): number {
  const cent = Math.max(0, n.centrality ?? 0);
  const freq = (n.frequency ?? 1) + 1;
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

function cycleAt<T>(items: readonly [T, ...T[]], index: number): T {
  const item = items[index % items.length];
  if (item === undefined) throw new Error("cycleAt received an empty cycle");
  return item;
}

export function buildGraphModel(
  data: GraphPayload,
  mode: ConceptosphereMode,
  theme: GraphTheme,
  reduceMotion: boolean,
): GraphModel {
  const comColor = new Map<number, string>();
  const comLabel = new Map<number, string>();
  const comSize  = new Map<number, number>();
  const comRgb   = new Map<number, [number, number, number]>();
  for (const c of data.communities) {
    const color = communityColor(c.id);
    comColor.set(c.id, color);
    comLabel.set(c.id, c.label);
    comSize.set(c.id, c.size);
    comRgb.set(c.id, communityRgb(c.id));
  }

  const graph = new Graph({ type: "undirected", multi: false });

  // Node sizing: sqrt(centrality) emphasizes anchor nodes while log(freq)
  // keeps the long tail visible. Books mode reserves room for the number badge.
  const sizedNodes = data.nodes.map((node) => ({ node, size: nodeSize(node, mode) }));
  const maxSize = sizedNodes.reduce((max, { size }) => Math.max(max, size), 0);
  const sizeThresh = maxSize * 0.62;

  // Seed each community in its own sector. Sector width ∝ sqrt(community
  // size), so big communities own more arc without crowding everything else.
  const comsBySize = [...data.communities].sort((a, b) => b.size - a.size);
  const sectorOf = new Map<number, { start: number; end: number; mid: number }>();
  const totalRoot = comsBySize.reduce((s, c) => s + Math.sqrt(c.size), 0);
  let cursor = 0;
  for (const c of comsBySize) {
    const span = (Math.sqrt(c.size) / totalRoot) * Math.PI * 2;
    sectorOf.set(c.id, { start: cursor, end: cursor + span, mid: cursor + span / 2 });
    cursor += span;
  }

  const nodesByCom = new Map<number, NodeJson[]>();
  for (const { node: n } of sizedNodes) {
    const arr = nodesByCom.get(n.community) ?? [];
    arr.push(n);
    nodesByCom.set(n.community, arr);
  }
  for (const arr of nodesByCom.values()) {
    arr.sort((a, b) => (b.centrality ?? 0) - (a.centrality ?? 0));
  }

  const RING_BASE = 22;
  const COM_SPREAD = mode === "books" ? 7.6 : 3.8;

  const comCentroid = new Map<number, { x: number; y: number }>();
  for (const c of comsBySize) {
    const sec = sectorOf.get(c.id)!;
    comCentroid.set(c.id, {
      x: Math.cos(sec.mid) * RING_BASE,
      y: Math.sin(sec.mid) * RING_BASE,
    });
  }

  sizedNodes.forEach(({ node: n, size }, i) => {
    const c = comCentroid.get(n.community)!;
    const sec = sectorOf.get(n.community)!;
    const members = nodesByCom.get(n.community)!;
    const inComIdx = members.findIndex((x) => x.id === n.id);
    const radial = Math.sqrt(inComIdx / Math.max(1, members.length)) * COM_SPREAD;
    const angSpan = Math.min(sec.end - sec.start, Math.PI * 0.6);
    const ang = sec.mid + (prng(i) - 0.5) * angSpan * 0.85;
    const r = radial + prng(i + 7000) * 0.6;
    const col = communityColor(n.community);

    const label = mode === "books" ? (n.title ?? n.label ?? n.id) : (n.label ?? n.id);
    const badge = mode === "books" ? String(n.number ?? "") : "";

    graph.addNode(n.id, {
      label,
      badge,
      x: c.x + Math.cos(ang) * r,
      y: c.y + Math.sin(ang) * r,
      size,
      _size: size,
      labelSize: labelSizeFor(size, maxSize),
      color: col,
      _color: col,
      community: n.community,
      frequency: n.frequency,
      centrality: n.centrality,
      degree: n.degree,
      title: n.title ?? n.label,
      tags: n.tags ?? [],
      top_books: n.top_books ?? [],
      top_concepts: n.top_concepts ?? [],
      top_similar: n.top_similar ?? [],
      top_similar_embed: n.top_similar_embed ?? [],
      number: n.number,
      slug: n.slug,
      kind: mode === "books" ? "book" : undefined,
      forceLabel: mode === "books" ? false : size >= sizeThresh,
    });
  });

  if (mode === "concepts") {
    for (const arr of nodesByCom.values()) {
      const k = arr.length >= 25 ? 2 : 1;
      for (const n of arr.slice(0, k)) {
        if (graph.hasNode(n.id)) graph.setNodeAttribute(n.id, "forceLabel", true);
      }
    }
  }

  let minW = Infinity, maxW = 0;
  for (const e of data.edges) {
    const w = e.weight ?? 1;
    if (w < minW) minW = w;
    if (w > maxW) maxW = w;
  }
  const wRange = Math.max(maxW - minW, 1e-9);

  const CROSS_KEEP = mode === "books" ? 6 : 3;
  const crossByPair = new Map<string, EdgeJson[]>();
  for (const e of data.edges) {
    if (!graph.hasNode(e.source) || !graph.hasNode(e.target)) continue;
    const sc = graph.getNodeAttribute(e.source, "community") as number;
    const tc = graph.getNodeAttribute(e.target, "community") as number;
    if (sc === tc) continue;
    const key = `${Math.min(sc, tc)}|${Math.max(sc, tc)}`;
    const arr = crossByPair.get(key) ?? [];
    arr.push(e);
    crossByPair.set(key, arr);
  }
  const visibleCross = new Set<string>();
  for (const arr of crossByPair.values()) {
    arr.sort((a, b) => (b.npmi ?? b.weight ?? 0) - (a.npmi ?? a.weight ?? 0));
    for (const e of arr.slice(0, CROSS_KEEP)) {
      visibleCross.add(`${e.source}|${e.target}`);
    }
  }

  for (const e of data.edges) {
    if (!graph.hasNode(e.source) || !graph.hasNode(e.target)) continue;
    if (graph.hasEdge(e.source, e.target)) continue;
    const sc = graph.getNodeAttribute(e.source, "community") as number;
    const tc = graph.getNodeAttribute(e.target, "community") as number;
    const within = sc === tc;
    const isBackbone = within || visibleCross.has(`${e.source}|${e.target}`);
    const w = e.weight ?? 1;
    const t = Math.pow(Math.max(0, (w - minW) / wRange), 0.55);
    const ca = comRgb.get(sc)!;
    const cb = comRgb.get(tc)!;
    const edge = edgeVisual(theme, mode, within, t, ca, cb);
    graph.addEdge(e.source, e.target, {
      size: edge.size,
      _size: edge.size,
      color: edge.color,
      _color: edge.color,
      _tone: t,
      weight: w,
      within,
      backbone: isBackbone,
      hidden: !isBackbone,
    });
  }

  applyCommunityLayout(graph, data, nodesByCom, comSize, mode, reduceMotion);
  return { graph, nodesByCom, comColor, comLabel, comSize, comRgb };
}

function applyCommunityLayout(
  graph: Graph,
  data: GraphPayload,
  nodesByCom: Map<number, NodeJson[]>,
  comSize: Map<number, number>,
  mode: ConceptosphereMode,
  reduceMotion: boolean,
): void {
  const comTies = new Map<number, Map<number, number>>();
  for (const c of data.communities) comTies.set(c.id, new Map());
  graph.forEachEdge((_e, attrs, _s, _t, sa, ta) => {
    const A = sa.community as number;
    const B = ta.community as number;
    if (A === B) return;
    const w = (attrs.weight as number | undefined) ?? 1;
    comTies.get(A)!.set(B, (comTies.get(A)!.get(B) ?? 0) + w);
    comTies.get(B)!.set(A, (comTies.get(B)!.get(A) ?? 0) + w);
  });

  const comGraph = new Graph({ type: "undirected", multi: false });
  data.communities.forEach((c) => {
    comGraph.addNode(String(c.id), {
      x: Math.cos((c.id / data.communities.length) * Math.PI * 2) * 10,
      y: Math.sin((c.id / data.communities.length) * Math.PI * 2) * 10,
      size: Math.sqrt(c.size) * 4,
    });
  });
  for (const [cid, ties] of comTies) {
    for (const [other, w] of ties) {
      if (cid < other && comGraph.hasNode(String(cid)) && comGraph.hasNode(String(other))) {
        comGraph.addEdge(String(cid), String(other), { weight: w });
      }
    }
  }

  if (comGraph.order >= 2 && comGraph.size >= 1) {
    const settings = forceAtlas2.inferSettings(comGraph);
    settings.gravity = 1.5;
    settings.scalingRatio = 40;
    settings.slowDown = 4;
    settings.strongGravityMode = true;
    settings.barnesHutOptimize = false;
    settings.linLogMode = false;
    settings.adjustSizes = true;
    settings.edgeWeightInfluence = 1.2;
    forceAtlas2.assign(comGraph, { iterations: 300, settings });
  }

  const comAngles: { id: number; ang: number }[] = [];
  comGraph.forEachNode((id, a) => {
    comAngles.push({ id: Number(id), ang: Math.atan2(a.y as number, a.x as number) });
  });
  comAngles.sort((a, b) => a.ang - b.ang);
  const totalWeight =
    comAngles.reduce((s, c) => s + Math.sqrt(comSize.get(c.id) ?? 0), 0) || 1;

  let acc = 0;
  const comPos = new Map<number, { x: number; y: number; angle: number }>();
  const tmp = new Map<number, number>();
  for (const c of comAngles) {
    const span = (Math.sqrt(comSize.get(c.id) ?? 0) / totalWeight) * Math.PI * 2;
    tmp.set(c.id, acc + span / 2);
    acc += span;
  }
  const TIERS = mode === "books" ? [260, 380] as const : [210, 330, 450] as const;
  comAngles.forEach((c, i) => {
    const ang = tmp.get(c.id)!;
    const sz = comSize.get(c.id) ?? 0;
    const baseR = cycleAt(TIERS, i);
    const ringR = baseR + Math.sqrt(sz / 50) * 12;
    comPos.set(c.id, { x: Math.cos(ang) * ringR, y: Math.sin(ang) * ringR, angle: ang });
  });

  for (const [cid, nodes] of nodesByCom) {
    const sub = new Graph({ type: "undirected", multi: false });
    nodes.forEach((n, i) => {
      if (!graph.hasNode(n.id)) return;
      const r = 1 + (i / Math.max(1, nodes.length - 1)) * 3.5;
      const ang = (i / Math.max(1, nodes.length)) * Math.PI * 2;
      sub.addNode(n.id, {
        x: Math.cos(ang) * r,
        y: Math.sin(ang) * r,
        size: graph.getNodeAttribute(n.id, "size") as number,
      });
    });
    graph.forEachEdge((_edge, attrs, s, t, sa, ta) => {
      if ((sa.community as number) === cid && (ta.community as number) === cid && sub.hasNode(s) && sub.hasNode(t)) {
        sub.addEdge(s, t, { weight: (attrs.weight as number | undefined) ?? 1 });
      }
    });
    if (sub.order > 2) {
      const settings = forceAtlas2.inferSettings(sub);
      settings.gravity = 1.2;
      settings.scalingRatio = mode === "books" ? 12 : 7;
      settings.slowDown = 6;
      settings.strongGravityMode = true;
      settings.adjustSizes = true;
      settings.linLogMode = true;
      settings.edgeWeightInfluence = 1.4;
      settings.barnesHutOptimize = false;
      forceAtlas2.assign(sub, { iterations: reduceMotion ? 120 : 300, settings });
    }
    const centroid = comPos.get(cid) ?? { x: 0, y: 0, angle: 0 };
    let subR = 0;
    sub.forEachNode((_id, a) => {
      subR = Math.max(subR, Math.hypot(a.x as number, a.y as number));
    });
    const targetR = mode === "books"
      ? Math.min(132, 32 + Math.sqrt(nodes.length) * 12.5)
      : Math.min(28, 6 + Math.sqrt(nodes.length) * 3);
    const subScale = subR > 0 ? targetR / subR : 1;
    sub.forEachNode((id, a) => {
      if (graph.hasNode(id)) {
        graph.setNodeAttribute(id, "x", (a.x as number) * subScale + centroid.x);
        graph.setNodeAttribute(id, "y", (a.y as number) * subScale + centroid.y);
      }
    });
  }

  relaxOverlaps(graph, {
    iterations: reduceMotion ? (mode === "books" ? 120 : 70) : (mode === "books" ? 260 : 150),
    margin: mode === "books" ? 9.5 : 2.0,
    ratio: mode === "books" ? 1.22 : 1.08,
  });
  if (mode === "books") {
    relaxCommunityCircles(graph, nodesByCom, {
      iterations: reduceMotion ? 45 : 90,
      margin: 22,
      ratio: 0.90,
      strength: 0.32,
    });
  }

  recenterGraph(graph);
}

function relaxOverlaps(
  graph: Graph,
  options: { iterations: number; margin: number; ratio: number },
): void {
  const nodes = graph.nodes();
  if (nodes.length < 2) return;

  for (let iter = 0; iter < options.iterations; iter++) {
    let moved = 0;
    const dx = new Map<string, number>();
    const dy = new Map<string, number>();

    for (let i = 0; i < nodes.length; i++) {
      const aId = nodes[i]!;
      const a = graph.getNodeAttributes(aId);
      const ax = a.x as number;
      const ay = a.y as number;
      const ar = ((a._size as number | undefined) ?? (a.size as number) ?? 1) + options.margin;

      for (let j = i + 1; j < nodes.length; j++) {
        const bId = nodes[j]!;
        const b = graph.getNodeAttributes(bId);
        const bx = b.x as number;
        const by = b.y as number;
        const br = ((b._size as number | undefined) ?? (b.size as number) ?? 1) + options.margin;
        let vx = bx - ax;
        let vy = by - ay;
        let dist = Math.hypot(vx, vy);
        if (dist < 0.001) {
          const seed = prng(i * 4099 + j);
          const angle = seed * Math.PI * 2;
          vx = Math.cos(angle) * 0.001;
          vy = Math.sin(angle) * 0.001;
          dist = 0.001;
        }
        const minDist = (ar + br) * options.ratio;
        if (dist >= minDist) continue;
        const push = (minDist - dist) * 0.5;
        const ux = vx / dist;
        const uy = vy / dist;
        dx.set(aId, (dx.get(aId) ?? 0) - ux * push);
        dy.set(aId, (dy.get(aId) ?? 0) - uy * push);
        dx.set(bId, (dx.get(bId) ?? 0) + ux * push);
        dy.set(bId, (dy.get(bId) ?? 0) + uy * push);
        moved = Math.max(moved, push);
      }
    }

    const damp = iter < 20 ? 0.55 : 0.38;
    for (const id of nodes) {
      const x = dx.get(id) ?? 0;
      const y = dy.get(id) ?? 0;
      if (x === 0 && y === 0) continue;
      graph.updateNodeAttribute(id, "x", (v) => (v as number) + x * damp);
      graph.updateNodeAttribute(id, "y", (v) => (v as number) + y * damp);
    }
    if (moved < 0.015) break;
  }
}

function relaxCommunityCircles(
  graph: Graph,
  nodesByCom: Map<number, NodeJson[]>,
  options: { iterations: number; margin: number; ratio: number; strength: number },
): void {
  const communities = [...nodesByCom.keys()];
  if (communities.length < 2) return;

  const circles = () => {
    const out = new Map<number, { x: number; y: number; r: number }>();
    for (const [cid, nodes] of nodesByCom) {
      let x = 0, y = 0, count = 0;
      for (const n of nodes) {
        if (!graph.hasNode(n.id)) continue;
        const a = graph.getNodeAttributes(n.id);
        x += a.x as number;
        y += a.y as number;
        count++;
      }
      if (!count) continue;
      x /= count;
      y /= count;
      let r = 0;
      for (const n of nodes) {
        if (!graph.hasNode(n.id)) continue;
        const a = graph.getNodeAttributes(n.id);
        const nodeR = ((a._size as number | undefined) ?? (a.size as number | undefined) ?? 1) * 0.72;
        r = Math.max(r, Math.hypot((a.x as number) - x, (a.y as number) - y) + nodeR);
      }
      out.set(cid, { x, y, r: r + options.margin });
    }
    return out;
  };

  for (let iter = 0; iter < options.iterations; iter++) {
    const current = circles();
    let moved = 0;
    const dx = new Map<number, number>();
    const dy = new Map<number, number>();

    for (let i = 0; i < communities.length; i++) {
      const aId = communities[i]!;
      const a = current.get(aId);
      if (!a) continue;
      for (let j = i + 1; j < communities.length; j++) {
        const bId = communities[j]!;
        const b = current.get(bId);
        if (!b) continue;
        let vx = b.x - a.x;
        let vy = b.y - a.y;
        let dist = Math.hypot(vx, vy);
        if (dist < 0.001) {
          const seed = prng(aId * 8191 + bId);
          const ang = seed * Math.PI * 2;
          vx = Math.cos(ang) * 0.001;
          vy = Math.sin(ang) * 0.001;
          dist = 0.001;
        }
        const minDist = (a.r + b.r) * options.ratio;
        if (dist >= minDist) continue;
        const push = (minDist - dist) * 0.5 * options.strength;
        const ux = vx / dist;
        const uy = vy / dist;
        dx.set(aId, (dx.get(aId) ?? 0) - ux * push);
        dy.set(aId, (dy.get(aId) ?? 0) - uy * push);
        dx.set(bId, (dx.get(bId) ?? 0) + ux * push);
        dy.set(bId, (dy.get(bId) ?? 0) + uy * push);
        moved = Math.max(moved, push);
      }
    }

    for (const cid of communities) {
      const shiftX = dx.get(cid) ?? 0;
      const shiftY = dy.get(cid) ?? 0;
      if (!shiftX && !shiftY) continue;
      for (const n of nodesByCom.get(cid) ?? []) {
        if (!graph.hasNode(n.id)) continue;
        graph.updateNodeAttribute(n.id, "x", (v) => (v as number) + shiftX);
        graph.updateNodeAttribute(n.id, "y", (v) => (v as number) + shiftY);
      }
    }

    if (moved < 0.025) break;
  }
}

function recenterGraph(graph: Graph): void {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  graph.forEachNode((_n, a) => {
    const x = a.x as number;
    const y = a.y as number;
    if (x < minX) minX = x;
    if (y < minY) minY = y;
    if (x > maxX) maxX = x;
    if (y > maxY) maxY = y;
  });
  const cx = (minX + maxX) / 2;
  const cy = (minY + maxY) / 2;
  graph.forEachNode((id, a) => {
    graph.setNodeAttribute(id, "x", (a.x as number) - cx);
    graph.setNodeAttribute(id, "y", (a.y as number) - cy);
  });
}

function prng(i: number): number {
  const x = Math.sin(i * 12.9898 + 78.233) * 43758.5453;
  return x - Math.floor(x);
}
