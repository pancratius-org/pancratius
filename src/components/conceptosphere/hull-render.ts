import type Graph from "graphology";
import type Sigma from "sigma";

import type { GraphTheme } from "./graph-theme";
import { communityColor } from "./palette";
import type { ConceptosphereMode, NodeJson } from "./runtime-types";

interface HullContext {
  stage: HTMLElement;
  hulls: SVGSVGElement;
}

interface HullSession {
  mode: ConceptosphereMode;
  graph: Graph;
  renderer: Sigma;
  theme: GraphTheme;
  nodesByCom: Map<number, NodeJson[]>;
  disposed: boolean;
}

export function drawHulls(ctx: HullContext, s: HullSession): void {
  if (s.disposed) return;
  while (ctx.hulls.firstChild) ctx.hulls.removeChild(ctx.hulls.firstChild);
  const box = ctx.stage.getBoundingClientRect();
  ctx.hulls.setAttribute("viewBox", `0 0 ${box.width} ${box.height}`);
  ctx.hulls.setAttribute("width", String(box.width));
  ctx.hulls.setAttribute("height", String(box.height));

  for (const [cid, nodes] of s.nodesByCom) {
    if (nodes.length < 3) continue;
    const pts: [number, number][] = [];
    for (const n of nodes) {
      if (!s.graph.hasNode(n.id)) continue;
      const a = s.graph.getNodeAttributes(n.id);
      const screen = s.renderer.graphToViewport({ x: a.x as number, y: a.y as number });
      if (Number.isFinite(screen.x) && Number.isFinite(screen.y)) pts.push([screen.x, screen.y]);
    }
    if (pts.length < 3) continue;
    const hull = convexHull(pts);
    if (hull.length < 3) continue;
    const cx = hull.reduce((sum, p) => sum + p[0], 0) / hull.length;
    const cy = hull.reduce((sum, p) => sum + p[1], 0) / hull.length;
    const pad = s.mode === "books" ? 40 : 28;
    const inflated = hull.map<[number, number]>(([x, y]) => {
      const dx = x - cx, dy = y - cy;
      const d = Math.hypot(dx, dy) || 1;
      return [x + (dx / d) * pad, y + (dy / d) * pad];
    });
    const color = communityColor(cid);
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", smoothPath(inflated));
    path.setAttribute("fill", color);
    path.setAttribute("fill-opacity", s.theme.hullFillOpacity);
    path.setAttribute("stroke", color);
    path.setAttribute("stroke-opacity", s.theme.hullStrokeOpacity);
    path.setAttribute("stroke-width", "1");
    path.setAttribute("stroke-linejoin", "round");
    path.setAttribute("data-com", String(cid));
    ctx.hulls.appendChild(path);
  }
}

function convexHull(points: [number, number][]): [number, number][] {
  const pts = points.slice().sort((a, b) => (a[0] === b[0] ? a[1] - b[1] : a[0] - b[0]));
  const cross = (O: [number, number], A: [number, number], B: [number, number]) =>
    (A[0] - O[0]) * (B[1] - O[1]) - (A[1] - O[1]) * (B[0] - O[0]);
  const lower: [number, number][] = [];
  for (const p of pts) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) lower.pop();
    lower.push(p);
  }
  const upper: [number, number][] = [];
  for (let i = pts.length - 1; i >= 0; i--) {
    const p = pts[i];
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) upper.pop();
    upper.push(p);
  }
  upper.pop(); lower.pop();
  return lower.concat(upper);
}

function smoothPath(pts: [number, number][]): string {
  if (pts.length < 3) return "";
  const n = pts.length;
  const get = (i: number) => pts[((i % n) + n) % n];
  let d = `M ${get(0)[0].toFixed(1)} ${get(0)[1].toFixed(1)} `;
  for (let i = 0; i < n; i++) {
    const p0 = get(i - 1), p1 = get(i), p2 = get(i + 1), p3 = get(i + 2);
    const t = 0.18;
    const c1x = p1[0] + (p2[0] - p0[0]) * t;
    const c1y = p1[1] + (p2[1] - p0[1]) * t;
    const c2x = p2[0] - (p3[0] - p1[0]) * t;
    const c2y = p2[1] - (p3[1] - p1[1]) * t;
    d += `C ${c1x.toFixed(1)} ${c1y.toFixed(1)} ${c2x.toFixed(1)} ${c2y.toFixed(1)} ${p2[0].toFixed(1)} ${p2[1].toFixed(1)} `;
  }
  return `${d}Z`;
}
