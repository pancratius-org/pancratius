import type { GraphNodeData } from "./graph-data.ts";
import { GRAPH_GEOMETRY_PROFILE } from "./graph-geometry-config.ts";
import type { ConceptGraph, ConceptRenderer } from "./graph-model.ts";
import type { GraphTheme } from "./graph-theme.ts";
import { communityColor } from "./palette.ts";
import type { ConceptosphereMode } from "./graph-types.ts";

type Point = [number, number];

interface HullLayerInput {
  stage: HTMLElement;
  hulls: SVGSVGElement;
  mode: ConceptosphereMode;
  graph: ConceptGraph;
  renderer: ConceptRenderer;
  theme: GraphTheme;
  nodesByCommunity: ReadonlyMap<number, readonly GraphNodeData[]>;
  isDisposed: () => boolean;
}

export interface HullLayer {
  readonly element: SVGSVGElement;
  paint: () => void;
  schedule: () => void;
  dispose: () => void;
}

export function createHullLayer(input: HullLayerInput): HullLayer {
  let hullRaf: number | null = null;

  const paint = () => {
    if (input.isDisposed()) return;
    paintHulls(input);
  };

  const schedule = () => {
    if (input.isDisposed() || hullRaf !== null) return;
    hullRaf = requestAnimationFrame(() => {
      hullRaf = null;
      paint();
    });
  };

  const camera = input.renderer.getCamera();
  camera.on("updated", schedule);
  schedule();

  return {
    element: input.hulls,
    paint,
    schedule,
    dispose: () => {
      camera.removeListener("updated", schedule);
      if (hullRaf !== null) cancelAnimationFrame(hullRaf);
      hullRaf = null;
      clearSvg(input.hulls);
    },
  };
}

function paintHulls(input: HullLayerInput): void {
  clearSvg(input.hulls);
  const box = input.stage.getBoundingClientRect();
  sizeHullLayer(input.hulls, box);

  for (const [communityId, nodes] of input.nodesByCommunity) {
    const path = communityHullPath(input, communityId, nodes);
    if (path) input.hulls.appendChild(path);
  }
}

function sizeHullLayer(hulls: SVGSVGElement, box: DOMRect): void {
  hulls.setAttribute("viewBox", `0 0 ${box.width} ${box.height}`);
  hulls.setAttribute("width", String(box.width));
  hulls.setAttribute("height", String(box.height));
}

function communityHullPath(
  input: HullLayerInput,
  communityId: number,
  nodes: readonly GraphNodeData[],
): SVGPathElement | null {
  if (nodes.length < 3) return null;
  const hull = convexHull(viewportPoints(input.graph, input.renderer, nodes));
  if (hull.length < 3) return null;
  const inflated = inflateHull(hull, GRAPH_GEOMETRY_PROFILE[input.mode].hull.paddingViewportPx);
  return svgHullPath(inflated, communityColor(communityId), communityId, input.theme);
}

function svgHullPath(
  points: readonly Point[],
  color: string,
  communityId: number,
  theme: GraphTheme,
): SVGPathElement {
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", smoothPath(points));
  path.setAttribute("fill", color);
  path.setAttribute("fill-opacity", theme.hullFillOpacity);
  path.setAttribute("stroke", color);
  path.setAttribute("stroke-opacity", theme.hullStrokeOpacity);
  path.setAttribute("stroke-width", "1");
  path.setAttribute("stroke-linejoin", "round");
  path.setAttribute("data-com", String(communityId));
  return path;
}

function viewportPoints(
  graph: ConceptGraph,
  renderer: ConceptRenderer,
  nodes: readonly GraphNodeData[],
): Point[] {
  const points: Point[] = [];
  for (const node of nodes) {
    if (!graph.hasNode(node.id)) continue;
    const attrs = graph.getNodeAttributes(node.id);
    const screen = renderer.graphToViewport({ x: attrs.x, y: attrs.y });
    if (Number.isFinite(screen.x) && Number.isFinite(screen.y)) points.push([screen.x, screen.y]);
  }
  return points;
}

function inflateHull(hull: readonly Point[], pad: number): Point[] {
  const centerX = hull.reduce((sum, point) => sum + point[0], 0) / hull.length;
  const centerY = hull.reduce((sum, point) => sum + point[1], 0) / hull.length;
  return hull.map<Point>(([x, y]) => {
    const dx = x - centerX;
    const dy = y - centerY;
    const distance = Math.hypot(dx, dy) || 1;
    return [x + (dx / distance) * pad, y + (dy / distance) * pad];
  });
}

function convexHull(points: Point[]): Point[] {
  const pts = points.slice().sort((a, b) => (a[0] === b[0] ? a[1] - b[1] : a[0] - b[0]));
  const cross = (origin: Point, a: Point, b: Point) =>
    (a[0] - origin[0]) * (b[1] - origin[1]) - (a[1] - origin[1]) * (b[0] - origin[0]);
  const lower: Point[] = [];
  for (const point of pts) {
    while (lower.length >= 2) {
      const [a, b] = lastTwo(lower);
      if (cross(a, b, point) > 0) break;
      lower.pop();
    }
    lower.push(point);
  }
  const upper: Point[] = [];
  for (const point of [...pts].reverse()) {
    while (upper.length >= 2) {
      const [a, b] = lastTwo(upper);
      if (cross(a, b, point) > 0) break;
      upper.pop();
    }
    upper.push(point);
  }
  upper.pop();
  lower.pop();
  return lower.concat(upper);
}

function lastTwo(points: readonly Point[]): [Point, Point] {
  const a = points.at(-2);
  const b = points.at(-1);
  if (a === undefined || b === undefined) {
    throw new Error("convex hull stack must contain at least two points");
  }
  return [a, b];
}

function pointAt(points: readonly Point[], index: number): Point {
  const point = points[((index % points.length) + points.length) % points.length];
  if (point === undefined) throw new Error("smooth hull path index exceeded point bounds");
  return point;
}

function smoothPath(points: readonly Point[]): string {
  if (points.length < 3) return "";
  const start = pointAt(points, 0);
  let path = `M ${start[0].toFixed(1)} ${start[1].toFixed(1)} `;
  for (let i = 0; i < points.length; i++) {
    const p0 = pointAt(points, i - 1);
    const p1 = pointAt(points, i);
    const p2 = pointAt(points, i + 1);
    const p3 = pointAt(points, i + 2);
    const tension = 0.18;
    const c1x = p1[0] + (p2[0] - p0[0]) * tension;
    const c1y = p1[1] + (p2[1] - p0[1]) * tension;
    const c2x = p2[0] - (p3[0] - p1[0]) * tension;
    const c2y = p2[1] - (p3[1] - p1[1]) * tension;
    path += `C ${c1x.toFixed(1)} ${c1y.toFixed(1)} ${c2x.toFixed(1)} ${c2y.toFixed(1)} ${p2[0].toFixed(1)} ${p2[1].toFixed(1)} `;
  }
  return `${path}Z`;
}

function clearSvg(svg: SVGSVGElement): void {
  while (svg.firstChild) svg.removeChild(svg.firstChild);
}
