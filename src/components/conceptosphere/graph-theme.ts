import type { ConceptosphereMode } from "./graph-types.ts";

type Rgb = [number, number, number];
type EdgeScope = "within" | "cross";

export interface GraphTheme {
  isLight: boolean;
  dimNode: string;
  dimEdge: string;
  focusEdge: string;
  defaultEdgeColor: string;
  labelColor: string;
  labelHalo: string;
  badgeHalo: string;
  badgeInk: string;
  calloutBg: string;
  calloutInk: string;
  focusRing: string;
  focusRingMuted: string;
  focusRingSoft: string;
  focusRingMutedSoft: string;
  focusCalloutBorder: string;
  focusCalloutBorderMuted: string;
  edgeInkRgb: Rgb;
  edgeNeutralRgb: Rgb;
  hullFillOpacity: string;
  hullStrokeOpacity: string;
  hullDimFillOpacity: string;
  hullDimStrokeOpacity: string;
}

const GRAPH_THEME_FALLBACKS = {
  dimNode: "rgba(70, 65, 55, 0.45)",
  dimEdge: "rgba(80, 70, 55, 0.04)",
  focusEdge: "rgba(233, 161, 66, 0.55)",
  defaultEdgeColor: "rgba(232, 227, 214, 0.10)",
  labelColor: "#f3eee0",
  labelHalo: "rgba(6, 8, 12, 0.55)",
  badgeHalo: "rgba(6, 8, 12, 0.75)",
  badgeInk: "#f3eee0",
  calloutBg: "rgba(9, 11, 16, 0.96)",
  calloutInk: "#f3eee0",
  focusRing: "rgba(233, 161, 66, 0.96)",
  focusRingMuted: "rgba(233, 161, 66, 0.82)",
  focusRingSoft: "rgba(233, 161, 66, 0.30)",
  focusRingMutedSoft: "rgba(233, 161, 66, 0.18)",
  focusCalloutBorder: "rgba(233, 161, 66, 0.72)",
  focusCalloutBorderMuted: "rgba(233, 161, 66, 0.50)",
  edgeInkRgb: [26, 18, 12] as [number, number, number],
  edgeNeutralRgb: [120, 110, 95] as [number, number, number],
  hullFillOpacity: "0.08",
  hullStrokeOpacity: "0.22",
  hullDimFillOpacity: "0.012",
  hullDimStrokeOpacity: "0.045",
} as const;

interface EdgeVisualPolicy {
  color: EdgeColorPolicy;
  alphaBase: number;
  alphaRange: number;
  sizeBase: number;
  sizeRange: number;
  premultiply: boolean;
}

type EdgeColorPolicy =
  | { kind: "source" }
  | { kind: "sourceInk"; inkMix: number }
  | { kind: "crossCommunity"; neutralMix: number };

const DARK_EDGE_POLICIES = {
  within: {
    color: { kind: "source" },
    alphaBase: 0.22,
    alphaRange: 0.55,
    sizeBase: 0.6,
    sizeRange: 5.0,
    premultiply: false,
  },
  cross: {
    color: { kind: "crossCommunity", neutralMix: 0.4 },
    alphaBase: 0.07,
    alphaRange: 0.28,
    sizeBase: 0.4,
    sizeRange: 3.0,
    premultiply: false,
  },
} as const satisfies Record<EdgeScope, EdgeVisualPolicy>;

const LIGHT_EDGE_POLICIES = {
  concepts: {
    within: {
      color: { kind: "sourceInk", inkMix: 0.20 },
      alphaBase: 0.52,
      alphaRange: 0.18,
      sizeBase: 0.62,
      sizeRange: 2.3,
      premultiply: true,
    },
    cross: {
      color: { kind: "sourceInk", inkMix: 0.12 },
      alphaBase: 0.24,
      alphaRange: 0.12,
      sizeBase: 0.30,
      sizeRange: 0.80,
      premultiply: true,
    },
  },
  books: {
    within: {
      color: { kind: "sourceInk", inkMix: 0.24 },
      alphaBase: 0.58,
      alphaRange: 0.20,
      sizeBase: 0.72,
      sizeRange: 2.5,
      premultiply: true,
    },
    cross: {
      color: { kind: "sourceInk", inkMix: 0.18 },
      alphaBase: 0.42,
      alphaRange: 0.18,
      sizeBase: 0.52,
      sizeRange: 1.6,
      premultiply: true,
    },
  },
} as const satisfies Record<ConceptosphereMode, Record<EdgeScope, EdgeVisualPolicy>>;

export function readGraphTheme(): GraphTheme {
  const isLight = document.documentElement.getAttribute("data-theme") === "light";
  return {
    isLight,
    dimNode: cssSigmaColor("--cs-dim-node", GRAPH_THEME_FALLBACKS.dimNode, isLight),
    dimEdge: cssSigmaColor("--cs-dim-edge", GRAPH_THEME_FALLBACKS.dimEdge, isLight),
    focusEdge: cssSigmaColor("--cs-focus-edge", GRAPH_THEME_FALLBACKS.focusEdge, isLight),
    defaultEdgeColor: cssSigmaColor("--cs-default-edge", GRAPH_THEME_FALLBACKS.defaultEdgeColor, isLight),
    labelColor: cssVar("--cs-label-color", GRAPH_THEME_FALLBACKS.labelColor),
    labelHalo: cssVar("--cs-label-halo", GRAPH_THEME_FALLBACKS.labelHalo),
    badgeHalo: cssVar("--cs-badge-halo", GRAPH_THEME_FALLBACKS.badgeHalo),
    badgeInk: cssVar("--cs-badge-ink", GRAPH_THEME_FALLBACKS.badgeInk),
    calloutBg: cssVar("--cs-callout-bg", GRAPH_THEME_FALLBACKS.calloutBg),
    calloutInk: cssVar("--cs-callout-ink", GRAPH_THEME_FALLBACKS.calloutInk),
    focusRing: cssVar("--cs-focus-ring", GRAPH_THEME_FALLBACKS.focusRing),
    focusRingMuted: cssVar("--cs-focus-ring-muted", GRAPH_THEME_FALLBACKS.focusRingMuted),
    focusRingSoft: cssVar("--cs-focus-ring-soft", GRAPH_THEME_FALLBACKS.focusRingSoft),
    focusRingMutedSoft: cssVar("--cs-focus-ring-muted-soft", GRAPH_THEME_FALLBACKS.focusRingMutedSoft),
    focusCalloutBorder: cssVar("--cs-focus-callout-border", GRAPH_THEME_FALLBACKS.focusCalloutBorder),
    focusCalloutBorderMuted: cssVar("--cs-focus-callout-border-muted", GRAPH_THEME_FALLBACKS.focusCalloutBorderMuted),
    edgeInkRgb: cssRgbTriplet("--cs-edge-ink-rgb", GRAPH_THEME_FALLBACKS.edgeInkRgb),
    edgeNeutralRgb: cssRgbTriplet("--cs-edge-neutral-rgb", GRAPH_THEME_FALLBACKS.edgeNeutralRgb),
    hullFillOpacity: cssVar("--cs-hull-fill-opacity", GRAPH_THEME_FALLBACKS.hullFillOpacity),
    hullStrokeOpacity: cssVar("--cs-hull-stroke-opacity", GRAPH_THEME_FALLBACKS.hullStrokeOpacity),
    hullDimFillOpacity: cssVar("--cs-hull-dim-fill-opacity", GRAPH_THEME_FALLBACKS.hullDimFillOpacity),
    hullDimStrokeOpacity: cssVar("--cs-hull-dim-stroke-opacity", GRAPH_THEME_FALLBACKS.hullDimStrokeOpacity),
  };
}

export function edgeVisual(
  theme: GraphTheme,
  mode: ConceptosphereMode,
  within: boolean,
  t: number,
  ca: Rgb,
  cb: Rgb,
): { color: string; size: number } {
  const policy = edgeVisualPolicy(theme, mode, within);
  const rgb = edgePolicyRgb(policy.color, theme, ca, cb);
  const alpha = policy.alphaBase + t * policy.alphaRange;
  const size = policy.sizeBase + t * policy.sizeRange;
  return {
    color: policy.premultiply ? sigmaRgba(rgb, alpha) : rgba(rgb, alpha),
    size,
  };
}

function edgeVisualPolicy(
  theme: GraphTheme,
  mode: ConceptosphereMode,
  within: boolean,
): EdgeVisualPolicy {
  const scope = within ? "within" : "cross";
  return theme.isLight ? LIGHT_EDGE_POLICIES[mode][scope] : DARK_EDGE_POLICIES[scope];
}

function edgePolicyRgb(
  policy: EdgeColorPolicy,
  theme: GraphTheme,
  sourceRgb: Rgb,
  targetRgb: Rgb,
): Rgb {
  if (policy.kind === "source") return sourceRgb;
  if (policy.kind === "sourceInk") return mixRgb(sourceRgb, theme.edgeInkRgb, policy.inkMix);
  return mixRgb(mixRgb(sourceRgb, targetRgb, 0.5), theme.edgeNeutralRgb, policy.neutralMix);
}

function mixRgb(
  a: Rgb,
  b: Rgb,
  t = 0.5,
): Rgb {
  return [a[0] * (1 - t) + b[0] * t, a[1] * (1 - t) + b[1] * t, a[2] * (1 - t) + b[2] * t];
}

function rgba([r, g, b]: Rgb, a: number): string {
  return `rgba(${Math.round(r)},${Math.round(g)},${Math.round(b)},${a.toFixed(3)})`;
}

function parseCssRgba(value: string): { rgb: Rgb; alpha: number } | null {
  const match = value.match(/^\s*rgba?\(\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)(?:\s*,\s*([0-9.]+))?\s*\)\s*$/i);
  if (!match) return null;
  const r = Number(match[1]);
  const g = Number(match[2]);
  const b = Number(match[3]);
  const a = match[4] === undefined ? 1 : Number(match[4]);
  if (![r, g, b, a].every(Number.isFinite)) return null;
  return {
    rgb: [
      Math.max(0, Math.min(255, r)),
      Math.max(0, Math.min(255, g)),
      Math.max(0, Math.min(255, b)),
    ],
    alpha: Math.max(0, Math.min(1, a)),
  };
}

// Sigma's WebGL layers use premultiplied-alpha blending. Canvas painters still
// want normal rgba(), but translucent colors sent into Sigma need RGB scaled by
// alpha or they wash out badly on the light paper background.
function sigmaRgba(rgb: Rgb, alpha: number): string {
  const a = Math.max(0, Math.min(1, alpha));
  return rgba([rgb[0] * a, rgb[1] * a, rgb[2] * a], a);
}

function cssSigmaColor(name: string, fallback: string, premultiply: boolean): string {
  const value = cssVar(name, fallback);
  if (!premultiply) return value;
  const parsed = parseCssRgba(value);
  if (parsed) return sigmaRgba(parsed.rgb, parsed.alpha);
  const fallbackParsed = parseCssRgba(fallback);
  return fallbackParsed ? sigmaRgba(fallbackParsed.rgb, fallbackParsed.alpha) : value;
}

function cssRgbTriplet(name: string, fallback: Rgb): Rgb {
  const raw = cssVar(name, "").replace(/,/g, " ").trim();
  const parts = raw.split(/\s+/).map(Number).filter(Number.isFinite);
  const [red, green, blue] = parts;
  if (red === undefined || green === undefined || blue === undefined) return fallback;
  return [
    Math.max(0, Math.min(255, red)),
    Math.max(0, Math.min(255, green)),
    Math.max(0, Math.min(255, blue)),
  ];
}

function cssVar(name: string, fallback: string): string {
  if (typeof getComputedStyle === "undefined") return fallback;
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}
