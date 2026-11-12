import { DEFAULT_STYLES } from "sigma";
import type { NodeDisplayData, SigmaOptions } from "sigma/types";

import type { ConceptEdgeAttributes, ConceptGraph, ConceptNodeAttributes, ConceptRenderer } from "./graph-model.ts";
import type { GraphTheme } from "./graph-theme.ts";
import type { ConceptosphereMode } from "./graph-types.ts";
import type { GraphInteractionState } from "./interaction-state.ts";

interface SigmaOptionsInput {
  mode: ConceptosphereMode;
  theme: GraphTheme;
  renderer: () => ConceptRenderer | null;
}

interface CanvasPainterInput {
  mode: ConceptosphereMode;
  graph: ConceptGraph;
  renderer: ConceptRenderer;
  theme: GraphTheme;
  state: GraphInteractionState;
}

interface CanvasPainterContext extends CanvasPainterInput {
  canvas: CanvasRenderingContext2D;
}

interface FocusPaintContext {
  graph: ConceptGraph;
  renderer: ConceptRenderer;
  theme: GraphTheme;
  canvas: CanvasRenderingContext2D;
  dims: { width: number; height: number };
  cameraRatio: number;
  zoomToSize: (ratio: number) => number;
}

export function sigmaOptionsFor(input: SigmaOptionsInput): SigmaOptions<ConceptNodeAttributes, ConceptEdgeAttributes> {
  const { mode, theme, renderer } = input;
  return {
    settings: {
      renderLabels: mode === "concepts",
      renderEdgeLabels: false,
      itemSizesReference: "screen",
      labelDensity: mode === "concepts" ? 1 : 0.4,
      labelGridCellSize: mode === "concepts" ? 68 : 140,
      labelRenderedSizeThreshold: mode === "books" ? 0 : 6.5,
      minCameraRatio: 0.08,
      maxCameraRatio: 8,
    },
    styles: {
      nodes: {
        ...DEFAULT_STYLES.nodes,
        labelColor: () => theme.labelColor,
        labelFont: `600 ${getComputedStyle(document.documentElement).getPropertyValue("--serif") || "Georgia, serif"}`,
        labelSize: (attributes) => {
          const cameraRatio = Math.max(0.001, renderer()?.getCamera().getState().ratio ?? 1);
          const zoomLift = Math.max(0, Math.min(1, (0.72 - cameraRatio) / 0.56));
          return attributes.labelSize * (1 + zoomLift * (theme.isLight ? 0.46 : 0.32));
        },
        backdropVisibility: "hidden",
      },
      edges: DEFAULT_STYLES.edges,
    },
  };
}

export function installCanvasPainters(input: CanvasPainterInput): void {
  const layer = input.renderer.createCanvas("annotations", {
    afterLayer: "stage",
    style: { pointerEvents: "none" },
  });
  layer.setAttribute("aria-hidden", "true");
  const canvas = layer.getContext("2d");
  if (!canvas) throw new Error("Graph annotations require a 2D canvas");
  const context = { ...input, canvas };
  input.renderer.on("afterRender", () => {
    const { width, height } = input.renderer.getDimensions();
    const ratio = window.devicePixelRatio || 1;
    const pixelWidth = Math.round(width * ratio);
    const pixelHeight = Math.round(height * ratio);
    if (layer.width !== pixelWidth || layer.height !== pixelHeight) {
      layer.width = pixelWidth;
      layer.height = pixelHeight;
      layer.style.width = `${width}px`;
      layer.style.height = `${height}px`;
    }
    canvas.setTransform(ratio, 0, 0, ratio, 0, 0);
    canvas.clearRect(0, 0, width, height);
    paintBookBadges(context);
    paintFocusDecoration(context);
  });
}

export function initialCameraRatio(stage: HTMLElement, mode: ConceptosphereMode): number {
  const box = stage.getBoundingClientRect();
  const aspect = box.height > 0 ? box.width / box.height : 1.6;
  let ratio = mode === "books" ? 1.25 : 1.08;
  if (aspect < 1.35) ratio += 0.10;
  if (aspect > 1.85) ratio -= 0.03;
  return Math.max(1.02, Math.min(1.34, ratio));
}

function paintBookBadges(input: CanvasPainterContext): void {
  const { mode, graph, renderer, theme } = input;
  if (mode !== "books") return;
  const context = input.canvas;
  const dims = renderer.getDimensions();
  const ratio = renderer.getCamera().getState().ratio;
  if (ratio > 2.6) return;

  context.save();
  context.textAlign = "center";
  context.textBaseline = "middle";
  graph.forEachNode((id, attrs) => {
    if (!attrs.bookNumberBadge) return;
    const displayData = renderer.getNodeDisplayData(id) as
      | (NodeDisplayData & { dimmed?: boolean })
      | undefined;
    if (!displayData) return;
    if (displayData.dimmed || displayData.color === theme.dimNode) return;

    const viewport = renderer.graphToViewport({ x: attrs.x, y: attrs.y });
    if (!isVisiblePoint(viewport, dims, 20)) return;

    const radiusPx = displayData.size;
    if (radiusPx < 7) return;
    const fontPx = Math.max(9, Math.min(15, radiusPx * 0.95));
    context.font = `500 ${fontPx}px var(--serif), "PT Serif", Georgia, serif`;
    context.lineWidth = Math.max(1, fontPx * 0.14);
    context.lineJoin = "round";
    context.miterLimit = 2;
    context.strokeStyle = theme.badgeHalo;
    context.fillStyle = theme.badgeInk;
    context.strokeText(attrs.bookNumberBadge, viewport.x, viewport.y + 0.5);
    context.fillText(attrs.bookNumberBadge, viewport.x, viewport.y + 0.5);
  });
  context.restore();
}

function paintFocusDecoration(input: CanvasPainterContext): void {
  const targets = focusTargets(input.state);
  if (!targets.length) return;

  const paint = focusPaintContext(input);

  paint.canvas.save();
  for (const target of targets) paintFocusTarget(paint, target);
  paint.canvas.restore();
}

function focusPaintContext(input: CanvasPainterContext): FocusPaintContext {
  const { graph, renderer, theme } = input;

  return {
    graph,
    renderer,
    theme,
    canvas: input.canvas,
    dims: renderer.getDimensions(),
    cameraRatio: Math.max(0.001, renderer.getCamera().getState().ratio || 1),
    zoomToSize: zoomToSizeFunction(renderer),
  };
}

function zoomToSizeFunction(renderer: ConceptRenderer): (ratio: number) => number {
  return renderer.getSetting("zoomToSizeRatioFunction");
}

function paintFocusTarget(
  paint: FocusPaintContext,
  target: { id: string; pinned: boolean },
): void {
  if (!paint.graph.hasNode(target.id)) return;
  const attrs = paint.graph.getNodeAttributes(target.id);
  const displayData = paint.renderer.getNodeDisplayData(target.id);
  if (!displayData) return;

  const viewport = paint.renderer.graphToViewport({ x: attrs.x, y: attrs.y });
  if (!isVisiblePoint(viewport, paint.dims, 60)) return;

  const radius = focusRadius(attrs.baseSize, target.pinned, paint);
  paintFocusRings(paint.canvas, paint.theme, viewport, radius, target.pinned);
  paintFocusCallout(paint.canvas, paint.theme, paint.dims, viewport, radius, target.pinned, attrs.title ?? attrs.label);
}

function focusRadius(graphSize: number, strong: boolean, paint: FocusPaintContext): number {
  const focusScale = strong ? 1.12 : 1.08;
  const sizeRatio = Math.max(0.001, paint.zoomToSize(paint.cameraRatio));
  return (graphSize * focusScale) / sizeRatio;
}

function focusTargets(state: GraphInteractionState): { id: string; pinned: boolean }[] {
  const targets: { id: string; pinned: boolean }[] = [];
  if (state.pinned) targets.push({ id: state.pinned, pinned: true });
  if (state.hovered && state.hovered !== state.pinned) targets.push({ id: state.hovered, pinned: false });
  return targets;
}

function paintFocusRings(
  context: CanvasRenderingContext2D,
  theme: GraphTheme,
  center: { x: number; y: number },
  radius: number,
  strong: boolean,
): void {
  const innerGap = Math.max(strong ? 3 : 2, Math.min(strong ? 12 : 9, radius * 0.06));
  const outerGap = Math.max(strong ? 8 : 6, Math.min(strong ? 26 : 20, radius * 0.14));
  const innerWidth = Math.max(1.4, Math.min(strong ? 4.5 : 3.6, radius * 0.025));
  const outerWidth = Math.max(3.0, Math.min(strong ? 11 : 8.5, radius * 0.055));

  context.beginPath();
  context.arc(center.x, center.y, radius + innerGap, 0, Math.PI * 2);
  context.strokeStyle = strong ? theme.focusRing : theme.focusRingMuted;
  context.lineWidth = innerWidth;
  context.stroke();

  context.beginPath();
  context.arc(center.x, center.y, radius + outerGap, 0, Math.PI * 2);
  context.strokeStyle = strong ? theme.focusRingSoft : theme.focusRingMutedSoft;
  context.lineWidth = outerWidth;
  context.stroke();
}

function paintFocusCallout(
  context: CanvasRenderingContext2D,
  theme: GraphTheme,
  dims: { width: number; height: number },
  center: { x: number; y: number },
  radius: number,
  strong: boolean,
  labelText: string,
): void {
  if (!labelText) return;
  const box = focusCalloutBox(context, dims, center, radius, strong, labelText);
  paintCalloutBox(context, theme, strong, box);
  context.fillStyle = theme.calloutInk;
  context.textBaseline = "middle";
  context.fillText(box.text, box.x + box.padX, box.y + box.height / 2 + 0.5);
}

interface FocusCalloutBox {
  text: string;
  x: number;
  y: number;
  width: number;
  height: number;
  padX: number;
}

function focusCalloutBox(
  context: CanvasRenderingContext2D,
  dims: { width: number; height: number },
  center: { x: number; y: number },
  radius: number,
  strong: boolean,
  labelText: string,
): FocusCalloutBox {
  const fontSize = strong ? 13.5 : 12.5;
  context.font = `${strong ? 600 : 500} ${fontSize}px var(--serif), "PT Serif", Georgia, serif`;
  const padX = 10;
  const padY = 5;
  const maxBoxWidth = Math.min(360, dims.width - 24);
  const innerMax = maxBoxWidth - padX * 2;
  const drawn = truncateCanvasText(context, labelText, innerMax);
  const textWidth = context.measureText(drawn).width;
  const boxWidth = textWidth + padX * 2;
  const boxHeight = fontSize + padY * 2;
  let boxX = center.x + radius + 10;
  if (boxX + boxWidth > dims.width - 8) boxX = center.x - radius - 10 - boxWidth;
  const boxY = Math.max(8, Math.min(dims.height - boxHeight - 8, center.y - boxHeight / 2));
  return { text: drawn, x: boxX, y: boxY, width: boxWidth, height: boxHeight, padX };
}

function paintCalloutBox(
  context: CanvasRenderingContext2D,
  theme: GraphTheme,
  strong: boolean,
  box: FocusCalloutBox,
): void {
  context.fillStyle = theme.calloutBg;
  context.strokeStyle = strong ? theme.focusCalloutBorder : theme.focusCalloutBorderMuted;
  context.lineWidth = strong ? 1.3 : 1;
  context.beginPath();
  if (typeof context.roundRect === "function") {
    context.roundRect(box.x, box.y, box.width, box.height, 2);
  } else {
    context.rect(box.x, box.y, box.width, box.height);
  }
  context.fill();
  context.stroke();
}

function truncateCanvasText(
  context: CanvasRenderingContext2D,
  labelText: string,
  maxWidth: number,
): string {
  if (context.measureText(labelText).width <= maxWidth) return labelText;

  let drawn = labelText;
  while (drawn.length > 1 && context.measureText(`${drawn}…`).width > maxWidth) {
    drawn = drawn.slice(0, -1);
  }
  return `${drawn.replace(/[\s\-—,:;]+$/, "")}…`;
}

function isVisiblePoint(
  point: { x: number; y: number },
  dims: { width: number; height: number },
  pad: number,
): boolean {
  return Number.isFinite(point.x)
    && Number.isFinite(point.y)
    && point.x >= -pad
    && point.x <= dims.width + pad
    && point.y >= -pad
    && point.y <= dims.height + pad;
}
