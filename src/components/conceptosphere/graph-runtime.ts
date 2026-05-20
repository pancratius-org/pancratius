// Sigma 3 + graphology runtime for /conceptosphere/.
//
// This module owns all canvas-side behaviour: data load, layout, painting,
// hover/click/legend/search wiring, and the analytical side panel. Imports
// resolve to `node_modules/` so the bundler ships one webgl renderer and
// nothing leaks to a CDN.
//
// The Astro `<script>` block writes a small `window.__cs` config (graph URLs,
// initial mode, slug→book pair info, cover URLs) before this module loads.
// We read it at boot, then never touch the global again.

import Graph from "graphology";
import forceAtlas2 from "graphology-layout-forceatlas2";
import Sigma from "sigma";
import type { Settings as SigmaSettings } from "sigma/settings";
import type { NodeDisplayData, EdgeDisplayData } from "sigma/types";

import type { ConceptosphereStrings } from "./strings";

// ─────────────────────────────────────────────────────────────────────
// Window contract written by the Astro shell.
//
// The route owns the locale and the strings; this runtime is a pure consumer.
// It never imports a locale dictionary or `i18n` — everything reader-facing
// arrives in `cfg.strings`.
// ─────────────────────────────────────────────────────────────────────

type Mode = "concepts" | "books";

interface PageConfig {
  conceptsUrl:  string;
  booksUrl:     string;
  initialMode:  Mode;
  locale:       "ru" | "en";
  /**
   * Per-book lookup keyed by the RU slug (the graph payload's identity).
   * `href` is the resolved work URL for the active page locale — when the
   * EN sibling exists it points at `/en/books/<en-slug>/`, otherwise at the
   * RU canonical `/books/<ru-slug>/`. The route's build-time resolver does
   * this with `findPair`, so the runtime never synthesises EN paths blindly.
   */
  bookSlugInfo: Record<string, { number: number; title: string; href: string }>;
  coverUrls:    Record<string, string>;
  strings:      ConceptosphereStrings;
}

declare global {
  interface Window {
    __csRenderer?: Sigma;
    __csGraph?:    Graph;
  }
}

function readPageConfig(): PageConfig | null {
  const el = document.getElementById("cs-config");
  if (!el || !el.textContent) return null;
  try {
    return JSON.parse(el.textContent) as PageConfig;
  } catch (err) {
    console.error("conceptosphere: failed to parse #cs-config", err);
    return null;
  }
}

// ─────────────────────────────────────────────────────────────────────
// Wire types — the public graph JSON payloads.
// ─────────────────────────────────────────────────────────────────────

interface Community {
  id:    number;
  label: string;
  size:  number;
}

interface NodeJson {
  id:                 string;
  label?:             string;
  lemma?:             string;
  title?:             string;
  slug?:              string;
  number?:            number;
  cover?:             string | null;
  tags?:              string[];
  community:          number;
  frequency?:         number;
  centrality?:        number;
  degree?:            number;
  top_concepts?:      { label?: string; lemma?: string; count?: number }[];
  top_books?:         { slug: string; title: string; count?: number }[];
  top_similar?:       SimilarRef[];
  top_similar_embed?: SimilarRef[];
}

interface SimilarRef {
  slug:   string;
  kind:   "book" | "poem" | "project";
  title:  string;
  cover?: string | null;
  weight?: number;
}

interface EdgeJson {
  source: string;
  target: string;
  weight: number;
  npmi?:  number;
}

interface GraphPayload {
  stats:       Record<string, number | string | undefined>;
  communities: Community[];
  nodes:       NodeJson[];
  edges:       EdgeJson[];
}

// ─────────────────────────────────────────────────────────────────────
// Palette + theme.
//
// The palette has to read clearly on both registers. The prototype was
// dark-only; here we keep one palette and let the void/paper background do
// the register switch. The 20 stops are muted-jewel and warm/cool alternate
// so adjacent communities never collide.
// ─────────────────────────────────────────────────────────────────────

const PALETTE = [
  "#c25e4f", "#d8a24a", "#e9c66a", "#6fa48a", "#4d8aa6",
  "#7c6bb8", "#b06aa0", "#c8a37a", "#a8485a", "#6e8fb5",
  "#93a85a", "#c98a4a", "#5fa3a3", "#a48cc8", "#b9755a",
  "#d9876b", "#5d997f", "#b5934f", "#8a7fbf", "#a3625f",
] as const;

// ─────────────────────────────────────────────────────────────────────
// Small helpers.
// ─────────────────────────────────────────────────────────────────────

function hex2rgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  return [
    parseInt(h.slice(0, 2), 16),
    parseInt(h.slice(2, 4), 16),
    parseInt(h.slice(4, 6), 16),
  ];
}
function mixRgb(
  a: [number, number, number],
  b: [number, number, number],
  t = 0.5,
): [number, number, number] {
  return [a[0] * (1 - t) + b[0] * t, a[1] * (1 - t) + b[1] * t, a[2] * (1 - t) + b[2] * t];
}
function rgba([r, g, b]: [number, number, number], a: number): string {
  return `rgba(${Math.round(r)},${Math.round(g)},${Math.round(b)},${a.toFixed(3)})`;
}

function parseCssRgba(value: string): { rgb: [number, number, number]; alpha: number } | null {
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
function sigmaRgba(rgb: [number, number, number], alpha: number): string {
  const a = Math.max(0, Math.min(1, alpha));
  return rgba([rgb[0] * a, rgb[1] * a, rgb[2] * a], a);
}

function sigmaCssRgba(value: string, fallbackRgb: [number, number, number], fallbackAlpha: number): string {
  const parsed = parseCssRgba(value);
  return parsed ? sigmaRgba(parsed.rgb, parsed.alpha) : sigmaRgba(fallbackRgb, fallbackAlpha);
}

function escapeHtml(s: string): string {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;",
  }[c]!));
}

// URL builders.
//
// For book targets the truth lives in `cfg.bookSlugInfo[slug].href`,
// pre-resolved at build time with `findPair`. The runtime reads it through
// `bookHrefFromCfg` rather than synthesising an `/en/...` path from the
// graph's RU slug — synthesising would 404 for the 43 books without an
// authored EN translation.
//
// Poem/project neighbours don't have a build-time href map (the graph never
// references them on book pages today), so they keep the synthesised form;
// every poem and every project exists in RU and in EN-if-authored, and the
// runtime currently never emits an EN poem/project link.
function bookHrefFromCfg(slug: string | undefined | null, cfg: PageConfig): string {
  if (!slug) return cfg.locale === "en" ? "/en/books/" : "/books/";
  const info = cfg.bookSlugInfo[slug];
  if (info?.href) return info.href;
  // Defence: an unknown slug means the page route forgot to register the
  // book in `bookSlugInfo`. Fall back to the RU canonical, which always
  // exists for every book in the graph.
  const clean = encodeURIComponent(String(slug).trim().replace(/^\/+|\/+$/g, "")).replace(/\./g, "%2E");
  return clean ? `/books/${clean}/` : "/books/";
}
function contentHref(
  ref: { slug?: string; kind?: string } | undefined,
  cfg: PageConfig,
): string {
  const kind = ref?.kind ?? "book";
  if (kind === "book") return bookHrefFromCfg(ref?.slug, cfg);
  const prefix = cfg.locale === "en" ? "/en" : "";
  const slug = String(ref?.slug ?? "").trim().replace(/^\/+|\/+$/g, "");
  const segment = kind === "poem" ? "poetry" : "projects";
  if (!slug) return `${prefix}/${segment}/`;
  const clean = encodeURIComponent(slug).replace(/\./g, "%2E");
  return `${prefix}/${segment}/${clean}/`;
}
function kindLabel(
  ref: { kind?: string } | undefined,
  strings: ConceptosphereStrings,
): string {
  const k = ref?.kind;
  if (k === "poem" || k === "project") return strings.kindLabels[k];
  return strings.kindLabels.book;
}

function prng(i: number): number {
  const x = Math.sin(i * 12.9898 + 78.233) * 43758.5453;
  return x - Math.floor(x);
}

// ─────────────────────────────────────────────────────────────────────
// Per-session state.
// ─────────────────────────────────────────────────────────────────────

interface Session {
  mode:     Mode;
  data:     GraphPayload;
  graph:    Graph;
  renderer: Sigma;
  nodesByCom: Map<number, NodeJson[]>;
  comColor: Map<number, string>;
  comLabel: Map<number, string>;
  comSize:  Map<number, number>;
  comRgb:   Map<number, [number, number, number]>;
  /** Themed dim colours, snapshotted at session build time. Per-session so
   *  a theme swap (which rebuilds the session) cannot drift a still-running
   *  reducer attached to the previous session. */
  dimNode:  string;
  dimEdge:  string;
  focusEdge: string;
  state: {
    hovered: string | null;
    pinned:  string | null;
    filterComs: Set<number>;
    search:  string;
  };
  applyHighlight: () => void;
  scheduleHullPaint: () => void;
  onSearchInput?: () => void;
  disposed: boolean;
}

// ─────────────────────────────────────────────────────────────────────
// Boot.
// ─────────────────────────────────────────────────────────────────────

export function bootGraph(): void {
  const cfg = readPageConfig();
  if (!cfg) {
    // Page shell missed its hook — surface as a console error and bail. The
    // mobile fallback still renders without us, so this is best-effort.
    console.error("conceptosphere: missing #cs-config payload");
    return;
  }
  if (window.matchMedia("(max-width: 700px)").matches) {
    // Mobile is the statically-rendered list — the graph never mounts.
    // Still keep the mode toggle and search wired so they drive the list.
    wireMobile(cfg);
    return;
  }
  void start(cfg);
}

async function start(cfg: PageConfig): Promise<void> {
  const stage = document.getElementById("cs-graph");
  const hulls = document.getElementById("cs-hulls") as unknown as SVGSVGElement | null;
  if (!stage || !hulls) return;

  const ctx: AppContext = {
    cfg,
    stage,
    hulls,
    panel:          requireEl("cs-panel"),
    panelBody:      requireEl("cs-panel-body"),
    panelClose:     requireEl("cs-panel-close"),
    desktopSearch:  requireEl<HTMLInputElement>("cs-search-desktop"),
    legendList:     requireEl("cs-legend-list"),
    legendClear:    requireEl<HTMLButtonElement>("cs-legend-clear"),
    legendTitle:    requireEl("cs-legend-title"),
    session: null,
    loadSerial: 0,
    reduceMotion: window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    dataCache: new Map(),
    activeMode: null,
  };

  ctx.panelClose.addEventListener("click", () => {
    if (!ctx.session) return;
    ctx.session.state.pinned = null;
    ctx.session.state.hovered = null;
    ctx.session.applyHighlight();
    hidePanel(ctx);
  });

  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K") && !e.altKey) {
      e.preventDefault();
      ctx.desktopSearch.focus();
      ctx.desktopSearch.select();
      return;
    }
    if (e.key === "/" && !e.metaKey && !e.ctrlKey && !e.altKey && !isEditableTarget(document.activeElement)) {
      e.preventDefault();
      ctx.desktopSearch.focus();
      ctx.desktopSearch.select();
      return;
    }
    if (e.key === "Escape") {
      if (document.activeElement === ctx.desktopSearch) ctx.desktopSearch.blur();
      const s = ctx.session;
      if (!s) return;
      ctx.desktopSearch.value = "";
      s.state.search = "";
      s.state.pinned = null;
      s.state.hovered = null;
      s.state.filterComs.clear();
      updateLegendUI(ctx, s);
      s.applyHighlight();
      hidePanel(ctx);
    }
  });

  window.addEventListener("resize", () => {
    ctx.session?.scheduleHullPaint();
  });

  // Mode toggles: every `[data-cs-mode-toggle] button[data-mode]` switches.
  document.querySelectorAll<HTMLButtonElement>("[data-cs-mode-toggle] button[data-mode]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const mode = btn.getAttribute("data-mode") as Mode | null;
      if (!mode) return;
      void setMode(ctx, mode).catch((err) => showGraphError(ctx, err));
    });
  });

  let themeRaf: number | null = null;
  const themeObserver = new MutationObserver(() => {
    if (themeRaf !== null) cancelAnimationFrame(themeRaf);
    themeRaf = requestAnimationFrame(() => {
      themeRaf = null;
      const mode = ctx.activeMode ?? cfg.initialMode;
      void setMode(ctx, mode).catch((err) => showGraphError(ctx, err));
    });
  });
  themeObserver.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["data-theme"],
  });

  try {
    await setMode(ctx, cfg.initialMode);
  } catch (err) {
    showGraphError(ctx, err);
  }
}

interface AppContext {
  cfg:           PageConfig;
  stage:         HTMLElement;
  hulls:         SVGSVGElement;
  panel:         HTMLElement;
  panelBody:     HTMLElement;
  panelClose:    HTMLElement;
  desktopSearch: HTMLInputElement;
  legendList:    HTMLElement;
  legendClear:   HTMLButtonElement;
  legendTitle:   HTMLElement;
  session:       Session | null;
  loadSerial:    number;
  reduceMotion:  boolean;
  dataCache:     Map<string, GraphPayload>;
  activeMode:    Mode | null;
}

function requireEl<T extends HTMLElement = HTMLElement>(id: string): T {
  const el = document.getElementById(id);
  if (!el) throw new Error(`conceptosphere: missing #${id}`);
  return el as T;
}

function isEditableTarget(el: Element | null): boolean {
  if (!el) return false;
  const html = el as HTMLElement;
  return html.isContentEditable || el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT";
}

// ─────────────────────────────────────────────────────────────────────
// Mode swap.
// ─────────────────────────────────────────────────────────────────────

async function setMode(ctx: AppContext, mode: Mode): Promise<void> {
  ctx.activeMode = mode;
  // Mark the active tab. role="tab" only accepts aria-selected (aria-pressed
  // is for role="button" toggles), so we use aria-selected exclusively here.
  document.querySelectorAll<HTMLButtonElement>("[data-cs-mode-toggle] button[data-mode]").forEach((b) => {
    const isActive = b.getAttribute("data-mode") === mode;
    b.setAttribute("aria-selected", isActive ? "true" : "false");
  });

  // Mobile: toggle the visible mode section in the statically-rendered list.
  document.querySelectorAll<HTMLElement>("[data-cs-mobile-mode]").forEach((el) => {
    el.hidden = el.getAttribute("data-cs-mobile-mode") !== mode;
  });

  // Refresh chrome copy + footer caption.
  const strings = ctx.cfg.strings;
  const modeCopy = strings.modes[mode];
  setText("cs-page-h1",   modeCopy.h1);
  setText("cs-page-lede", modeCopy.lede);
  setText("cs-page-meth", modeCopy.meth);
  setText("cs-legend-title", strings.legendTitle);

  // Desktop graph only — early bail when we're on a phone.
  if (window.matchMedia("(max-width: 700px)").matches) {
    return;
  }

  ctx.desktopSearch.placeholder = modeCopy.searchPlaceholder;
  ctx.desktopSearch.value = "";

  const serial = ++ctx.loadSerial;
  destroySession(ctx);
  ctx.legendList.innerHTML = "";
  ctx.legendClear.hidden = true;
  ctx.panelBody.innerHTML = "";
  ctx.panel.classList.remove("is-open", "is-pinned");

  const url = mode === "books" ? ctx.cfg.booksUrl : ctx.cfg.conceptsUrl;
  let data: GraphPayload;
  try {
    data = await fetchJson(ctx, url);
  } catch (err) {
    if (serial !== ctx.loadSerial) return;
    throw err;
  }
  if (serial !== ctx.loadSerial) return;

  const stats = data.stats ?? {};
  const nNodes =
    (typeof stats.kept_nodes === "number" ? stats.kept_nodes :
     typeof stats.books === "number"      ? stats.books      :
     data.nodes.length);
  const nEdges = typeof stats.edges === "number" ? stats.edges : data.edges.length;
  const nComs  = typeof stats.communities === "number" ? stats.communities : data.communities.length;
  setText(
    "cs-counts",
    `${nNodes} ${modeCopy.countsNoun} · ${nEdges} ${strings.edgesNoun} · ${nComs} ${strings.communitiesNoun}`,
  );

  const next = buildSession(ctx, data, mode);
  if (serial !== ctx.loadSerial) {
    next.disposed = true;
    try { next.renderer.kill(); } catch { /* ignore */ }
    ctx.stage.replaceChildren();
    return;
  }
  ctx.session = next;
  wireInteractions(ctx, next);
  next.applyHighlight();
}

async function fetchJson(ctx: AppContext, url: string): Promise<GraphPayload> {
  const cached = ctx.dataCache.get(url);
  if (cached) return cached;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${ctx.cfg.strings.loadErrorPrefix}: ${url} (${res.status})`);
  const data = (await res.json()) as GraphPayload;
  applyLocalizedBookTitles(ctx, data);
  ctx.dataCache.set(url, data);
  return data;
}

/**
 * Replace RU book titles in a freshly-fetched payload with the locale's
 * authored titles. The graph JSON only carries RU; the page passes the
 * localised title map in `cfg.bookSlugInfo`. Applied to top-level book
 * nodes and to each book node's `top_similar*` neighbour titles so panel
 * rendering and label painting see one consistent title per slug.
 */
function applyLocalizedBookTitles(ctx: AppContext, data: GraphPayload): void {
  const info = ctx.cfg.bookSlugInfo;
  if (!info) return;
  const titleFor = (slug: string | undefined): string | null =>
    slug && info[slug]?.title ? info[slug].title : null;
  for (const n of data.nodes) {
    const t = titleFor(n.slug ?? n.id);
    if (t) n.title = t;
    for (const ref of n.top_similar ?? []) {
      const rt = titleFor(ref.slug);
      if (rt) ref.title = rt;
    }
    for (const ref of n.top_similar_embed ?? []) {
      const rt = titleFor(ref.slug);
      if (rt) ref.title = rt;
    }
  }
}

function setText(id: string, text: string): void {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function destroySession(ctx: AppContext): void {
  const s = ctx.session;
  if (!s) return;
  s.disposed = true;
  if (s.onSearchInput) {
    ctx.desktopSearch.removeEventListener("input", s.onSearchInput);
  }
  try { s.renderer.kill(); } catch { /* ignore */ }
  if (window.__csRenderer === s.renderer) delete window.__csRenderer;
  if (window.__csGraph === s.graph) delete window.__csGraph;
  ctx.session = null;
  ctx.stage.replaceChildren();
  // Also clear the hull overlay so old paths don't linger on swap.
  while (ctx.hulls.firstChild) ctx.hulls.removeChild(ctx.hulls.firstChild);
}

function showGraphError(ctx: AppContext, err: unknown): void {
  console.error(err);
  destroySession(ctx);
  const msg = escapeHtml(err instanceof Error ? err.message : String(err));
  ctx.stage.innerHTML =
    `<p class="cs-load-error">${escapeHtml(ctx.cfg.strings.loadErrorPrefix)}: ${msg}</p>`;
}

// ─────────────────────────────────────────────────────────────────────
// Build a session (graph + Sigma + sector layout + painters).
// ─────────────────────────────────────────────────────────────────────

// Dim values are snapshotted into the Session at build time so the values can
// never drift between two sessions that were built under different themes.
// Light-mode node dimming is sent to Sigma's WebGL node program, so it uses
// sigmaRgba() instead of the CSS token's ordinary rgba().
const DIM_NODE_FALLBACK = "rgba(70, 65, 55, 0.45)";
const DIM_EDGE_FALLBACK = "rgba(80, 70, 55, 0.04)";
const FOCUS_EDGE_DARK = "rgba(233, 161, 66, 0.55)";

function buildSession(ctx: AppContext, data: GraphPayload, mode: Mode): Session {
  // Snapshot themed dim values for this session. Sigma copies these into the
  // node colour buffer the moment the reducer runs; reading once here and
  // storing on the Session means a live session never sees a mutated value
  // (mode swap rebuilds the session anyway).
  const isLightTheme = document.documentElement.getAttribute("data-theme") === "light";
  const dimNodeCss = cssVar("--cs-dim-node", DIM_NODE_FALLBACK);
  const dimNode = isLightTheme
    ? sigmaCssRgba(dimNodeCss, [92, 78, 58], 0.52)
    : dimNodeCss;
  const dimEdge = cssVar("--cs-dim-edge", DIM_EDGE_FALLBACK);
  const focusEdge = isLightTheme
    ? sigmaRgba([154, 89, 24], 0.58)
    : FOCUS_EDGE_DARK;

  const state = {
    hovered: null as string | null,
    pinned:  null as string | null,
    filterComs: new Set<number>(),
    search: "",
  };

  const comColor = new Map<number, string>();
  const comLabel = new Map<number, string>();
  const comSize  = new Map<number, number>();
  const comRgb   = new Map<number, [number, number, number]>();
  for (const c of data.communities) {
    const color = PALETTE[c.id % PALETTE.length];
    comColor.set(c.id, color);
    comLabel.set(c.id, c.label);
    comSize.set(c.id, c.size);
    comRgb.set(c.id, hex2rgb(color));
  }

  const graph = new Graph({ type: "undirected", multi: false });

  // Node sizing — sqrt(centrality) emphasises gravity-well nodes, log(freq)
  // keeps the long tail readable. Books mode uses a tighter range so the
  // number badge stays legible at min size.
  const sizes = data.nodes.map((n) => {
    const cent = Math.max(0, n.centrality ?? 0);
    const freq = (n.frequency ?? 1) + 1;
    if (mode === "books") {
      return Math.max(11, Math.min(32, 9 + Math.sqrt(cent) * 90 + Math.log10(freq) * 1.6));
    }
    return Math.max(2.4, Math.min(32, Math.sqrt(cent) * 130 + Math.log10(freq) * 0.9));
  });
  const maxSize = Math.max(...sizes);
  const sizeThresh = maxSize * 0.62;
  const labelSizes = sizes.map((s) => {
    if (s >= maxSize * 0.78) return 18;
    if (s >= maxSize * 0.55) return 15.5;
    if (s >= maxSize * 0.36) return 13.5;
    return 12.5;
  });

  // Seed each community in its own sector. Sector width ∝ sqrt(community
  // size), so big communities own more arc, but a single dominant cluster
  // can't crowd everything else out.
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
  for (const n of data.nodes) {
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

  data.nodes.forEach((n, i) => {
    const c = comCentroid.get(n.community)!;
    const sec = sectorOf.get(n.community)!;
    const members = nodesByCom.get(n.community)!;
    const inComIdx = members.findIndex((x) => x.id === n.id);
    const radial = Math.sqrt(inComIdx / Math.max(1, members.length)) * COM_SPREAD;
    const angSpan = Math.min(sec.end - sec.start, Math.PI * 0.6);
    const ang = sec.mid + (prng(i) - 0.5) * angSpan * 0.85;
    const r = radial + prng(i + 7000) * 0.6;
    const col = PALETTE[n.community % PALETTE.length];

    const label = mode === "books" ? (n.title ?? n.label ?? n.id) : (n.label ?? n.id);
    const badge = mode === "books" ? String(n.number ?? "") : "";

    graph.addNode(n.id, {
      label,
      badge,
      x: c.x + Math.cos(ang) * r,
      y: c.y + Math.sin(ang) * r,
      size: sizes[i],
      _size: sizes[i],
      labelSize: labelSizes[i],
      color: col,
      _color: col,
      community: n.community,
      frequency: n.frequency,
      centrality: n.centrality,
      degree: n.degree,
      title: n.title ?? n.label,
      cover: n.cover ?? null,
      tags: n.tags ?? [],
      top_books: n.top_books ?? [],
      top_concepts: n.top_concepts ?? [],
      top_similar: n.top_similar ?? [],
      top_similar_embed: n.top_similar_embed ?? [],
      number: n.number,
      slug: n.slug,
      kind: mode === "books" ? "book" : undefined,
      forceLabel: mode === "books" ? false : sizes[i] >= sizeThresh,
    });
  });

  // In concepts mode we surface the anchor of each community (top 1 or 2)
  // as always-on labels. In books mode every disc has a number badge, so
  // textual labels are reserved for hover/focus.
  if (mode === "concepts") {
    for (const arr of nodesByCom.values()) {
      const k = arr.length >= 25 ? 2 : 1;
      for (const n of arr.slice(0, k)) {
        if (graph.hasNode(n.id)) graph.setNodeAttribute(n.id, "forceLabel", true);
      }
    }
  }

  // ── edges ────────────────────────────────────────────────────────
  let minW = Infinity, maxW = 0;
  for (const e of data.edges) {
    const w = e.weight ?? 1;
    if (w < minW) minW = w;
    if (w > maxW) maxW = w;
  }
  const wRange = Math.max(maxW - minW, 1e-9);

  // Backbone pruning: keep all within-community edges, and only the top-K
  // cross-community edges per (a, b) community pair. Otherwise long-tail
  // crosshatch hides the cluster structure.
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
    const paperInk: [number, number, number] = [26, 18, 12];
    const darkNeutral: [number, number, number] = [120, 110, 95];
    const withinRgb = isLightTheme ? mixRgb(ca, paperInk, mode === "concepts" ? 0.20 : 0.24) : ca;
    const crossRgb = isLightTheme
      ? mixRgb(ca, paperInk, mode === "concepts" ? 0.12 : 0.18)
      : mixRgb(mixRgb(ca, cb, 0.5), darkNeutral, 0.4);
    const withinAlpha = isLightTheme
      ? (mode === "books" ? 0.58 + t * 0.20 : 0.52 + t * 0.18)
      : 0.22 + t * 0.55;
    const crossAlpha = isLightTheme
      ? (mode === "books" ? 0.42 + t * 0.18 : 0.24 + t * 0.12)
      : 0.07 + t * 0.28;
    const edgeColor = within
      ? (isLightTheme ? sigmaRgba(withinRgb, withinAlpha) : rgba(withinRgb, withinAlpha))
      : (isLightTheme ? sigmaRgba(crossRgb, crossAlpha) : rgba(crossRgb, crossAlpha));
    const sizeWithin = isLightTheme
      ? (mode === "books" ? 0.72 + t * 2.5 : 0.62 + t * 2.3)
      : 0.6 + t * 5.0;
    const sizeCross = isLightTheme
      ? (mode === "books" ? 0.52 + t * 1.6 : 0.30 + t * 0.80)
      : 0.4 + t * 3.0;
    graph.addEdge(e.source, e.target, {
      size: within ? sizeWithin : sizeCross,
      _size: within ? sizeWithin : sizeCross,
      color: edgeColor,
      _color: edgeColor,
      weight: w,
      within,
      backbone: isBackbone,
      hidden: !isBackbone,
    });
  }

  // ── multi-level layout ──────────────────────────────────────────
  // 1) Lay out the *community graph* to discover good angular order, then
  //    2) lay out each community's subgraph around its centroid. FA2 on
  //    everything-at-once never produces this much cluster separation on a
  //    graph this densely cross-linked.
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
  const TIERS = mode === "books" ? [260, 380] : [210, 330, 450];
  comAngles.forEach((c, i) => {
    const ang = tmp.get(c.id)!;
    const sz = comSize.get(c.id) ?? 0;
    const baseR = TIERS[i % TIERS.length];
    const ringR = baseR + Math.sqrt(sz / 50) * 12;
    comPos.set(c.id, { x: Math.cos(ang) * ringR, y: Math.sin(ang) * ringR, angle: ang });
  });

  // 2) per-community FA2 around centroid.
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
      forceAtlas2.assign(sub, { iterations: ctx.reduceMotion ? 120 : 300, settings });
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
    iterations: ctx.reduceMotion ? (mode === "books" ? 120 : 70) : (mode === "books" ? 260 : 150),
    margin: mode === "books" ? 9.5 : 2.0,
    ratio: mode === "books" ? 1.22 : 1.08,
  });
  if (mode === "books") {
    relaxCommunityCircles(graph, nodesByCom, {
      iterations: ctx.reduceMotion ? 45 : 90,
      margin: 22,
      ratio: 0.90,
      strength: 0.32,
    });
  }

  // Recenter at origin so the camera target {0.5, 0.5} actually lands on the
  // graph centroid, not on whatever the FA2 cumulative drift produced.
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

  // ── Sigma renderer ──────────────────────────────────────────────
  let rendererRef: Sigma | null = null;
  const settings: Partial<SigmaSettings> = {
    renderEdgeLabels: false,
    defaultEdgeColor: isLightTheme
      ? sigmaRgba([80, 58, 34], 0.18)
      : "rgba(232, 227, 214, 0.10)",
    labelColor: { color: cssVar("--cs-label-color", "#f3eee0") },
    labelFont: 'var(--serif), Georgia, serif',
    labelSize: 13.5,
    labelWeight: "600",
    labelDensity: mode === "concepts" ? 1 : 0.4,
    labelGridCellSize: mode === "concepts" ? 68 : 140,
    labelRenderedSizeThreshold: mode === "books" ? 0 : 6.5,
    minCameraRatio: 0.08,
    maxCameraRatio: 8,
    zIndex: true,
    // Sigma's stock hover pill is a white blob that clashes with the
    // dark/light palette; we paint our own focus chrome via afterRender.
    defaultDrawNodeHover: () => { /* no-op */ },
    defaultDrawNodeLabel: (context, displayData, drawSettings) => {
      if (!displayData.label) return;
      if (mode === "books") return;
      const baseLabelSize = (displayData as NodeDisplayData & { labelSize?: number }).labelSize
        ?? drawSettings.labelSize;
      const cameraRatio = Math.max(0.001, rendererRef?.getCamera().getState().ratio ?? 1);
      const zoomLift = Math.max(0, Math.min(1, (0.72 - cameraRatio) / 0.56));
      const labelScale = 1 + zoomLift * (isLightTheme ? 0.46 : 0.32);
      const labelSize = baseLabelSize * labelScale;
      const font = drawSettings.labelFont;
      const weight = drawSettings.labelWeight;
      context.font = `${weight} ${labelSize}px ${font}`;
      const label = displayData.label;
      const x = displayData.x + displayData.size + 3 + zoomLift * 2;
      const y = displayData.y + labelSize / 3;
      context.lineWidth = (isLightTheme ? 3.4 : 2.5) * Math.min(1.24, labelScale);
      context.strokeStyle = cssVar("--cs-label-halo", "rgba(6, 8, 12, 0.55)");
      context.lineJoin = "round";
      context.miterLimit = 2;
      context.strokeText(label, x, y);
      context.fillStyle = (drawSettings.labelColor as { color: string }).color ?? "#f3eee0";
      context.fillText(label, x, y);
    },
  };

  const renderer = new Sigma(graph, ctx.stage, settings);
  rendererRef = renderer;

  // Sigma mounts a stack of `<canvas>` children under the stage. They're
  // pure presentation — every meaningful surface is exposed through the
  // outer <figure> ARIA label, the SidePanel, the Legend, and the mobile
  // list. Mark each canvas presentational so screen readers don't expose
  // them as orphan graphics.
  for (const canvas of Object.values(renderer.getCanvases())) {
    if (canvas instanceof HTMLCanvasElement) {
      canvas.setAttribute("aria-hidden", "true");
      canvas.setAttribute("role", "presentation");
    }
  }

  // ── badge + focus painters ─────────────────────────────────────
  function paintBookBadges(): void {
    if (mode !== "books") return;
    const canvases = renderer.getCanvases();
    const layer = canvases.labels ?? canvases.hovers;
    if (!layer) return;
    const c2d = layer.getContext("2d");
    if (!c2d) return;
    const dims = renderer.getDimensions();
    const ratio = renderer.getCamera().getState().ratio;
    if (ratio > 2.6) return;
    c2d.save();
    c2d.textAlign = "center";
    c2d.textBaseline = "middle";
    graph.forEachNode((id, attrs) => {
      const badge = attrs.badge as string | undefined;
      if (!badge) return;
      const dd = renderer.getNodeDisplayData(id) as
        | (NodeDisplayData & { _dim?: boolean })
        | undefined;
      if (!dd) return;
      if (dd._dim || dd.color === dimNode) return;
      const vp = renderer.graphToViewport({ x: attrs.x as number, y: attrs.y as number });
      if (!Number.isFinite(vp.x) || !Number.isFinite(vp.y)) return;
      if (vp.x < -20 || vp.x > dims.width + 20) return;
      if (vp.y < -20 || vp.y > dims.height + 20) return;
      const radiusPx = (dd.size as number | undefined) ?? (attrs.size as number);
      if (radiusPx < 7) return;
      const fontPx = Math.max(9, Math.min(15, radiusPx * 0.95));
      c2d.font = `500 ${fontPx}px var(--serif), "PT Serif", Georgia, serif`;
      c2d.lineWidth = Math.max(1, fontPx * 0.14);
      c2d.lineJoin = "round";
      c2d.miterLimit = 2;
      c2d.strokeStyle = cssVar("--cs-badge-halo", "rgba(6, 8, 12, 0.75)");
      c2d.fillStyle = cssVar("--cs-badge-ink", "#f3eee0");
      c2d.strokeText(badge, vp.x, vp.y + 0.5);
      c2d.fillText(badge, vp.x, vp.y + 0.5);
    });
    c2d.restore();
  }
  renderer.on("afterRender", paintBookBadges);

  function paintFocusDecoration(): void {
    if (!state.hovered && !state.pinned) return;
    const canvases = renderer.getCanvases();
    const layer = canvases.labels ?? canvases.hovers;
    if (!layer) return;
    const c2d = layer.getContext("2d");
    if (!c2d) return;
    const dims = renderer.getDimensions();
    const cameraRatio = Math.max(0.001, renderer.getCamera().getState().ratio || 1);
    const targets: { id: string; pinned: boolean }[] = [];
    if (state.pinned) targets.push({ id: state.pinned, pinned: true });
    if (state.hovered && state.hovered !== state.pinned) {
      targets.push({ id: state.hovered, pinned: false });
    }
    if (!targets.length) return;

    const zoomToSizeFn =
      (renderer.getSetting("zoomToSizeRatioFunction") as ((r: number) => number) | undefined) ?? Math.sqrt;

    c2d.save();
    for (const t of targets) {
      if (!graph.hasNode(t.id)) continue;
      const attrs = graph.getNodeAttributes(t.id);
      const dd = renderer.getNodeDisplayData(t.id);
      if (!dd) continue;
      const vp = renderer.graphToViewport({ x: attrs.x as number, y: attrs.y as number });
      if (!Number.isFinite(vp.x) || !Number.isFinite(vp.y)) continue;
      if (vp.x < -60 || vp.x > dims.width + 60 || vp.y < -60 || vp.y > dims.height + 60) continue;

      const strong = t.pinned;
      const graphSize = (attrs._size ?? attrs.size ?? dd.size ?? 6) as number;
      const focusScale = strong ? 1.12 : 1.08;
      const sizeRatio = Math.max(0.001, zoomToSizeFn(cameraRatio));
      const r = (graphSize * focusScale) / sizeRatio;
      const innerGap = Math.max(strong ? 3 : 2, Math.min(strong ? 12 : 9, r * 0.06));
      const outerGap = Math.max(strong ? 8 : 6, Math.min(strong ? 26 : 20, r * 0.14));
      const innerWidth = Math.max(1.4, Math.min(strong ? 4.5 : 3.6, r * 0.025));
      const outerWidth = Math.max(3.0, Math.min(strong ? 11 : 8.5, r * 0.055));

      c2d.beginPath();
      c2d.arc(vp.x, vp.y, r + innerGap, 0, Math.PI * 2);
      c2d.strokeStyle = strong ? "rgba(233, 161, 66, 0.96)" : "rgba(233, 161, 66, 0.82)";
      c2d.lineWidth = innerWidth;
      c2d.stroke();

      c2d.beginPath();
      c2d.arc(vp.x, vp.y, r + outerGap, 0, Math.PI * 2);
      c2d.strokeStyle = strong ? "rgba(233, 161, 66, 0.30)" : "rgba(233, 161, 66, 0.18)";
      c2d.lineWidth = outerWidth;
      c2d.stroke();

      const labelText = (attrs.title as string | undefined) ?? (attrs.label as string | undefined) ?? "";
      if (!labelText) continue;

      const fontSize = strong ? 13.5 : 12.5;
      c2d.font = `${strong ? 600 : 500} ${fontSize}px var(--serif), "PT Serif", Georgia, serif`;
      const padX = 10;
      const padY = 5;
      const maxBoxW = Math.min(360, dims.width - 24);
      const innerMax = maxBoxW - padX * 2;
      let drawn = labelText;
      let textW = c2d.measureText(drawn).width;
      if (textW > innerMax) {
        while (drawn.length > 1 && c2d.measureText(`${drawn}…`).width > innerMax) {
          drawn = drawn.slice(0, -1);
        }
        drawn = drawn.replace(/[\s\-—,:;]+$/, "") + "…";
        textW = c2d.measureText(drawn).width;
      }
      const boxW = textW + padX * 2;
      const boxH = fontSize + padY * 2;
      let boxX = vp.x + r + 10;
      if (boxX + boxW > dims.width - 8) boxX = vp.x - r - 10 - boxW;
      const boxY = Math.max(8, Math.min(dims.height - boxH - 8, vp.y - boxH / 2));

      c2d.fillStyle = cssVar("--cs-callout-bg", strong ? "rgba(9,11,16,0.97)" : "rgba(6,8,12,0.94)");
      c2d.strokeStyle = strong ? "rgba(233, 161, 66, 0.72)" : "rgba(233, 161, 66, 0.50)";
      c2d.lineWidth = strong ? 1.3 : 1;
      c2d.beginPath();
      const roundRect = (c2d as CanvasRenderingContext2D & { roundRect?: (x: number, y: number, w: number, h: number, r: number) => void }).roundRect;
      if (typeof roundRect === "function") {
        roundRect.call(c2d, boxX, boxY, boxW, boxH, 2);
      } else {
        c2d.rect(boxX, boxY, boxW, boxH);
      }
      c2d.fill();
      c2d.stroke();

      c2d.fillStyle = cssVar("--cs-callout-ink", "#f3eee0");
      c2d.textBaseline = "middle";
      c2d.fillText(drawn, boxX + padX, boxY + boxH / 2 + 0.5);
    }
    c2d.restore();
  }
  renderer.on("afterRender", paintFocusDecoration);

  window.__csRenderer = renderer;
  window.__csGraph = graph;

  renderer.getCamera().setState({
    x: 0.5,
    y: 0.5,
    angle: 0,
    ratio: initialCameraRatio(ctx.stage, mode),
  });

  const session: Session = {
    mode, data, graph, renderer, nodesByCom,
    comColor, comLabel, comSize, comRgb,
    dimNode, dimEdge, focusEdge,
    state,
    applyHighlight: () => { /* set below */ },
    scheduleHullPaint: () => { /* set below */ },
    disposed: false,
  };
  return session;
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

function initialCameraRatio(stage: HTMLElement, mode: Mode): number {
  const box = stage.getBoundingClientRect();
  const aspect = box.height > 0 ? box.width / box.height : 1.6;
  let ratio = mode === "books" ? 1.25 : 1.08;
  if (aspect < 1.35) ratio += 0.10;
  if (aspect > 1.85) ratio -= 0.03;
  return Math.max(1.02, Math.min(1.34, ratio));
}

function cssVar(name: string, fallback: string): string {
  if (typeof getComputedStyle === "undefined") return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

// ─────────────────────────────────────────────────────────────────────
// Convex-hull overlay around each community.
// ─────────────────────────────────────────────────────────────────────

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

function drawHulls(ctx: AppContext, s: Session): void {
  if (s.disposed) return;
  while (ctx.hulls.firstChild) ctx.hulls.removeChild(ctx.hulls.firstChild);
  const box = ctx.stage.getBoundingClientRect();
  const isLightTheme = document.documentElement.getAttribute("data-theme") === "light";
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
    const color = PALETTE[cid % PALETTE.length];
    const fillOpacity = isLightTheme ? (s.mode === "books" ? "0.060" : "0.065") : "0.07";
    const strokeOpacity = isLightTheme ? (s.mode === "books" ? "0.155" : "0.160") : "0.18";
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", smoothPath(inflated));
    path.setAttribute("fill", color);
    path.setAttribute("fill-opacity", fillOpacity);
    path.setAttribute("stroke", color);
    path.setAttribute("stroke-opacity", strokeOpacity);
    path.setAttribute("stroke-width", "1");
    path.setAttribute("stroke-linejoin", "round");
    path.setAttribute("data-com", String(cid));
    ctx.hulls.appendChild(path);
  }
}

// ─────────────────────────────────────────────────────────────────────
// Interactions: hover/click/legend/search.
// ─────────────────────────────────────────────────────────────────────

function wireInteractions(ctx: AppContext, s: Session): void {
  const { graph, renderer, state } = s;

  function neighborhood(node: string): Set<string> {
    const set = new Set<string>([node]);
    graph.forEachNeighbor(node, (n) => set.add(n));
    return set;
  }

  function matchesSearch(attrs: Record<string, unknown>): boolean {
    if (!state.search) return true;
    const q = state.search.toLowerCase();
    if (((attrs.label as string | undefined) ?? "").toLowerCase().includes(q)) return true;
    if (((attrs.title as string | undefined) ?? "").toLowerCase().includes(q)) return true;
    if (s.mode === "books") {
      const tc = (attrs.top_concepts as { label?: string; lemma?: string }[] | undefined) ?? [];
      for (const c of tc) {
        if ((c.label ?? "").toLowerCase().includes(q)) return true;
        if ((c.lemma ?? "").toLowerCase().includes(q)) return true;
      }
    }
    return false;
  }

  s.applyHighlight = function applyHighlight(): void {
    const focus = state.pinned ?? state.hovered;
    const focusSet = focus ? neighborhood(focus) : null;
    const hasFocus = !!focusSet;
    const hasFilter = state.filterComs.size > 0;
    const hasSearch = !!state.search;
    const showFocusNeighborLabels = s.mode === "concepts" && !!state.pinned;

    renderer.setSetting("nodeReducer", (node, data) => {
      type NodeOut = Partial<NodeDisplayData> & {
        forceLabel?: boolean; _dim?: boolean; _size?: number;
        community?: number; _color?: string;
      };
      const out: NodeOut = { ...(data as NodeOut) };
      let dim = false;
      if (hasFilter && !state.filterComs.has(data.community as number)) dim = true;
      if (hasSearch && !matchesSearch(data as Record<string, unknown>)) dim = true;
      if (hasFocus && focusSet && !focusSet.has(node)) dim = true;
      if (dim) {
        out.color = s.dimNode;
        out.forceLabel = false;
        out.zIndex = 0;
        out._dim = true;
      } else {
        out.color = data._color as string;
        out.zIndex = (hasFocus && focusSet!.has(node)) ? 2 : 1;
        if (hasFocus) {
          out.forceLabel = showFocusNeighborLabels && node !== focus && focusSet!.has(node);
        } else {
          if (hasFilter && state.filterComs.has(data.community as number)) out.forceLabel = true;
          if (hasSearch && matchesSearch(data as Record<string, unknown>)) out.forceLabel = true;
        }
        out._dim = false;
      }
      if (state.pinned === node) {
        out.color = data._color as string;
        out.size = (data._size as number) * 1.12;
        out.forceLabel = false;
        out.zIndex = 4;
        out._dim = false;
      } else if (state.hovered === node) {
        out.color = data._color as string;
        out.size = (data._size as number) * 1.08;
        out.forceLabel = false;
        out.zIndex = 3;
        out._dim = false;
      }
      return out;
    });

    renderer.setSetting("edgeReducer", (edge, data) => {
      type EdgeOut = Partial<EdgeDisplayData> & {
        backbone?: boolean; within?: boolean; _color?: string; _size?: number;
      };
      const out: EdgeOut = { ...(data as EdgeOut) };
      const [src, tgt] = graph.extremities(edge);
      const sa = graph.getNodeAttributes(src);
      const ta = graph.getNodeAttributes(tgt);
      let dim = false;
      if (hasFilter && (!state.filterComs.has(sa.community as number) || !state.filterComs.has(ta.community as number))) dim = true;
      if (hasSearch && !(matchesSearch(sa) || matchesSearch(ta))) dim = true;
      if (hasFocus && focusSet && !(focusSet.has(src) && focusSet.has(tgt))) dim = true;
      if (dim) {
        if (hasFilter || hasSearch || hasFocus) { out.hidden = true; return out; }
        if (!data.backbone) { out.hidden = true; return out; }
        out.color = s.dimEdge;
        out.size = (data._size as number) * 0.5;
        out.hidden = false;
      } else if (hasFocus) {
        out.color = s.focusEdge;
        out.size = (data._size as number) * 1.4;
        out.zIndex = 2;
        out.hidden = false;
      } else if (hasFilter && state.filterComs.has(sa.community as number) && state.filterComs.has(ta.community as number)) {
        out.color = data._color as string;
        out.size = data._size as number;
        out.zIndex = 1;
        out.hidden = false;
      } else {
        if (!data.backbone) { out.hidden = true; return out; }
        out.color = data._color as string;
        out.size = data._size as number;
        out.zIndex = data.within ? 1 : 0;
        out.hidden = false;
      }
      return out;
    });

    // Hull dimming mirrors the focus/filter state.
    ctx.hulls.querySelectorAll("path").forEach((p) => {
      const cid = Number(p.getAttribute("data-com"));
      let highlight = false;
      if (hasFilter) highlight = state.filterComs.has(cid);
      else if (hasFocus && focusSet) {
        for (const n of focusSet) {
          if ((graph.getNodeAttribute(n, "community") as number) === cid) {
            highlight = true;
            break;
          }
        }
      } else highlight = true;
      const isLightTheme = document.documentElement.getAttribute("data-theme") === "light";
      p.setAttribute("fill-opacity", highlight ? (isLightTheme ? "0.070" : "0.08") : "0.012");
      p.setAttribute("stroke-opacity", highlight ? (isLightTheme ? "0.18" : "0.22") : "0.045");
    });
    renderer.refresh();
  };

  renderer.on("enterNode", ({ node }) => {
    state.hovered = node;
    s.applyHighlight();
    if (!state.pinned) showPanel(ctx, s, node, false);
  });
  renderer.on("leaveNode", () => {
    state.hovered = null;
    s.applyHighlight();
    if (!state.pinned) hidePanel(ctx);
  });
  renderer.on("clickNode", ({ node }) => {
    state.pinned = node;
    state.hovered = node;
    s.applyHighlight();
    showPanel(ctx, s, node, true);
  });
  renderer.on("clickStage", () => {
    if (state.pinned) {
      state.pinned = null;
      state.hovered = null;
      s.applyHighlight();
      hidePanel(ctx);
    }
  });

  s.onSearchInput = () => {
    state.search = ctx.desktopSearch.value.trim();
    s.applyHighlight();
  };
  ctx.desktopSearch.addEventListener("input", s.onSearchInput);

  // Legend list — render once per session. Children are real <button> elements
  // wrapped by a `role="group"` container (set in Legend.astro). An earlier
  // shape (`<ul>` with `<li role="button">`) failed axe's "list contains only
  // <li>" rule because role=button strips list-item semantics from the child.
  ctx.legendList.innerHTML = "";
  const sortedComs = [...s.data.communities].sort((a, b) => b.size - a.size);
  for (const c of sortedComs) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "cs-legend-item";
    btn.dataset.com = String(c.id);
    btn.setAttribute("aria-pressed", "false");
    btn.innerHTML = `
      <span class="cs-sw" style="background:${s.comColor.get(c.id)}"></span>
      <span>${escapeHtml(c.label)}</span>
      <span class="cs-sz">${c.size}</span>`;
    const toggle = () => {
      if (state.filterComs.has(c.id)) state.filterComs.delete(c.id);
      else state.filterComs.add(c.id);
      updateLegendUI(ctx, s);
      s.applyHighlight();
    };
    btn.addEventListener("click", toggle);
    ctx.legendList.appendChild(btn);
  }
  ctx.legendClear.onclick = () => {
    state.filterComs.clear();
    updateLegendUI(ctx, s);
    s.applyHighlight();
  };

  // Hull repaint on every camera tick (rAF-throttled).
  let hullRaf: number | null = null;
  s.scheduleHullPaint = () => {
    if (s.disposed) return;
    if (hullRaf !== null) return;
    hullRaf = requestAnimationFrame(() => {
      hullRaf = null;
      if (s.disposed) return;
      drawHulls(ctx, s);
    });
  };
  renderer.getCamera().on("updated", s.scheduleHullPaint);
  requestAnimationFrame(() => drawHulls(ctx, s));
}

function updateLegendUI(ctx: AppContext, s: Session): void {
  [...ctx.legendList.children].forEach((node) => {
    const li = node as HTMLElement;
    const cid = Number(li.dataset.com);
    const selected = s.state.filterComs.has(cid);
    li.classList.toggle("is-muted", s.state.filterComs.size > 0 && !selected);
    li.classList.toggle("is-selected", selected);
    li.setAttribute("aria-pressed", selected ? "true" : "false");
  });
  ctx.legendClear.hidden = s.state.filterComs.size === 0;
}

// ─────────────────────────────────────────────────────────────────────
// Side panel rendering. Mode-aware. Returns innerHTML strings; HTML escaping
// is applied to every interpolated value.
// ─────────────────────────────────────────────────────────────────────

function showPanel(ctx: AppContext, s: Session, nodeId: string, pinned: boolean): void {
  const attrs = s.graph.getNodeAttributes(nodeId) as Record<string, unknown>;
  ctx.panelBody.innerHTML = s.mode === "books"
    ? renderBookPanel(ctx, s, attrs, nodeId)
    : renderConceptPanel(ctx, s, attrs, nodeId);
  ctx.panel.classList.add("is-open");
  ctx.panel.classList.toggle("is-pinned", pinned);
}

function hidePanel(ctx: AppContext): void {
  ctx.panel.classList.remove("is-open", "is-pinned");
}

function renderConceptPanel(
  ctx: AppContext,
  s: Session,
  a: Record<string, unknown>,
  nodeId: string,
): string {
  const strings = ctx.cfg.strings;
  const numberLocale = strings.numberLocale;
  const community = a.community as number;
  const comName = s.comLabel.get(community)
    ?? strings.clusterFallbackLabel.replace("{n}", String(community));
  const sw = s.comColor.get(community) ?? "#888";
  const top = (a.top_books as { slug: string; title: string; count?: number }[] | undefined) ?? [];
  const books = top.slice(0, 5).map((b) => {
    const coverUrl = ctx.cfg.coverUrls[`book:${b.slug}`];
    const cover = coverUrl
      ? `<img class="cs-cover" loading="lazy" src="${attr(coverUrl)}" alt="" onerror="this.classList.add('is-miss');this.removeAttribute('src');" />`
      : `<span class="cs-cover is-miss" aria-hidden="true"></span>`;
    return `<li>
      ${cover}
      <div class="cs-b-meta">
        <a class="cs-b-title" href="${bookHrefFromCfg(b.slug, ctx.cfg)}">${escapeHtml(b.title)}</a>
        <div class="cs-b-count">${(b.count ?? 0).toLocaleString(numberLocale)} ${escapeHtml(strings.mentionsSuffix)}</div>
      </div>
    </li>`;
  }).join("");
  const freq = (a.frequency as number | undefined) ?? 0;
  const cent = (a.centrality as number | undefined) ?? 0;
  return `
    <div class="cs-com-tag"><span class="cs-sw" style="background:${attr(sw)}"></span><span>${escapeHtml(comName)}</span></div>
    <h3>${escapeHtml((a.label as string) ?? "")}</h3>
    <dl class="cs-stats">
      <dt>${escapeHtml(strings.statFrequency)}</dt><dd>${freq.toLocaleString(numberLocale)}</dd>
      <dt>${escapeHtml(strings.statCentrality)}</dt><dd>${(cent * 1000).toFixed(2)}‰</dd>
      <dt>${escapeHtml(strings.statConnections)}</dt><dd>${s.graph.degree(nodeId)}</dd>
    </dl>
    <p class="cs-books-h">${escapeHtml(strings.conceptTopBooksHeading)}</p>
    <ol class="cs-books">${books}</ol>`;
}

function renderBookPanel(
  ctx: AppContext,
  s: Session,
  a: Record<string, unknown>,
  _nodeId: string,
): string {
  const strings = ctx.cfg.strings;
  const numberLocale = strings.numberLocale;
  const community = a.community as number;
  const comName = s.comLabel.get(community)
    ?? strings.clusterFallbackLabel.replace("{n}", String(community));
  const sw = s.comColor.get(community) ?? "#888";
  const slug = (a.slug as string | undefined) ?? "";
  const coverUrl = ctx.cfg.coverUrls[`book:${slug}`];
  const selfLink = contentHref({ slug, kind: "book" }, ctx.cfg);
  const coverEl = coverUrl
    ? `<a class="cs-big-cover-link" href="${attr(selfLink)}" aria-label="${attr(strings.openBookLabel)}"><img class="cs-big-cover" loading="lazy" src="${attr(coverUrl)}" alt="" onerror="this.classList.add('is-miss');this.removeAttribute('src');" /></a>`
    : `<a class="cs-big-cover-link" href="${attr(selfLink)}" aria-label="${attr(strings.openBookLabel)}"><span class="cs-big-cover is-miss" aria-hidden="true"></span></a>`;

  const tags = ((a.tags as string[] | undefined) ?? []);
  const tagsHtml = tags.length
    ? `<ul class="cs-tags">${tags.map((t) => `<li>${escapeHtml(t)}</li>`).join("")}</ul>`
    : "";

  const topConcepts = ((a.top_concepts as { label?: string; lemma?: string; count?: number }[] | undefined) ?? []);
  const conceptsHtml = topConcepts.slice(0, 10).map((tc) => `
    <li>${escapeHtml(tc.label ?? tc.lemma ?? "")}<span class="cs-n">${(tc.count ?? 0).toLocaleString(numberLocale)}</span></li>
  `).join("");

  const tfItems  = ((a.top_similar       as SimilarRef[] | undefined) ?? []).slice(0, 5);
  const semItems = ((a.top_similar_embed as SimilarRef[] | undefined) ?? []).slice(0, 5);
  const semSlugs = new Set(semItems.map((b) => b.slug));
  const tfSlugs  = new Set(tfItems.map((b) => b.slug));

  const tfHtml  = tfItems.map((b) => renderSimilarRow(ctx, b, semSlugs)).join("");
  const semHtml = semItems.map((b) => renderSimilarRow(ctx, b, tfSlugs)).join("");
  const hasAny  = tfItems.length || semItems.length;

  const similarBlock = hasAny ? `
    ${tfItems.length ? `
      <p class="cs-books-h">${escapeHtml(strings.similarByConceptsHeading)}</p>
      <ol class="cs-books cs-similar-tf">${tfHtml}</ol>` : ""}
    ${semItems.length ? `
      <p class="cs-books-h">${escapeHtml(strings.similarByMeaningHeading)}</p>
      <ol class="cs-books cs-similar-sem">${semHtml}</ol>` : ""}
    ${(tfItems.length && semItems.length) ? `<p class="cs-conv-foot"><span class="cs-conv" aria-hidden="true">★</span>${escapeHtml(strings.convergenceFoot)}</p>` : ""}
  ` : "";

  return `
    <div class="cs-com-tag"><span class="cs-sw" style="background:${attr(sw)}"></span><span>${escapeHtml(comName)}</span></div>
    <div class="cs-book-hero">
      ${coverEl}
      <div class="cs-h-meta">
        <div class="cs-h-number">${escapeHtml(strings.bookNumberPrefix)} ${(a.number as number | undefined) ?? "—"}</div>
        <h3 style="margin-top:4px"><a class="cs-book-title-link" href="${attr(selfLink)}">${escapeHtml((a.title as string | undefined) ?? (a.label as string | undefined) ?? "")}</a></h3>
        ${tagsHtml}
      </div>
    </div>
    <p class="cs-books-h">${escapeHtml(strings.bookTopConceptsHeading)}</p>
    <ul class="cs-concepts">${conceptsHtml}</ul>
    ${similarBlock}`;
}

function renderSimilarRow(
  ctx: AppContext,
  ref: SimilarRef,
  convergentSet: Set<string>,
): string {
  const strings = ctx.cfg.strings;
  const key = `${ref.kind ?? "book"}:${ref.slug}`;
  const coverUrl = ctx.cfg.coverUrls[key];
  const cover = coverUrl
    ? `<img class="cs-cover" loading="lazy" src="${attr(coverUrl)}" alt="" onerror="this.classList.add('is-miss');this.removeAttribute('src');" />`
    : `<span class="cs-cover is-miss" aria-hidden="true"></span>`;
  const star = convergentSet.has(ref.slug)
    ? `<span class="cs-conv" title="${attr(strings.convergenceLabel)}" aria-label="${attr(strings.convergenceLabel)}">★</span>`
    : "";
  const pct = ((ref.weight ?? 0) * 100).toFixed(0);
  const prefix = (ref.kind ?? "book") === "book" ? "" : `${kindLabel(ref, strings)} · `;
  const href = contentHref({ kind: ref.kind ?? "book", slug: ref.slug }, ctx.cfg);
  const caption = strings.similarityCaption.replace("{pct}", pct);
  return `<li>
    ${cover}
    <div class="cs-b-meta">
      <div class="cs-b-title-row"><a class="cs-b-title" href="${attr(href)}">${escapeHtml(ref.title)}</a>${star}</div>
      <div class="cs-b-count">${prefix}${escapeHtml(caption)}</div>
    </div>
  </li>`;
}

function attr(s: string): string {
  return escapeHtml(s);
}

// ─────────────────────────────────────────────────────────────────────
// Mobile path. The list itself is statically rendered by Astro; the runtime
// only wires up the mode toggle, search filter, and cluster chips.
// ─────────────────────────────────────────────────────────────────────

function wireMobile(cfg: PageConfig): void {
  const root = document.getElementById("cs-mobile");
  if (!root) return;

  const allModes: Mode[] = ["concepts", "books"];

  // Resolve everything the inner functions close over before the initial
  // `applyMobileMode(initial)` call below. The inner `applyMobileFilters`
  // closes over `mobileSearch` and `filterSets`; declaring either of them
  // later puts us in a TDZ ReferenceError on the very first paint.
  const mobileSearch = document.getElementById("cs-search-mobile") as HTMLInputElement | null;
  const filterSets: Record<Mode, Set<number>> = { concepts: new Set(), books: new Set() };

  // 1) Mode toggle (the toggle buttons live in the page-level mobile toolbar).
  applyMobileMode(cfg.initialMode);
  document.querySelectorAll<HTMLButtonElement>("[data-cs-mode-toggle] button[data-mode]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const m = btn.getAttribute("data-mode") as Mode | null;
      if (!m) return;
      applyMobileMode(m);
    });
  });

  function applyMobileMode(mode: Mode): void {
    const modeCopy = cfg.strings.modes[mode];
    document.querySelectorAll<HTMLButtonElement>("[data-cs-mode-toggle] button[data-mode]").forEach((b) => {
      const active = b.getAttribute("data-mode") === mode;
      b.setAttribute("aria-selected", active ? "true" : "false");
    });
    for (const m of allModes) {
      const section = document.querySelector<HTMLElement>(`[data-cs-mobile-mode="${m}"]`);
      if (!section) continue;
      section.hidden = m !== mode;
    }
    if (mobileSearch) {
      mobileSearch.placeholder = modeCopy.searchPlaceholder;
      mobileSearch.value = "";
      applyMobileFilters(mode);
    }
    // Update header copy/footer methodology for parity with desktop.
    setText("cs-page-h1",   modeCopy.h1);
    setText("cs-page-lede", modeCopy.lede);
    setText("cs-page-meth", modeCopy.meth);
  }

  // 2) Search input filters node rows by data-search attribute.
  mobileSearch?.addEventListener("input", () => {
    const current = currentMobileMode();
    if (current) applyMobileFilters(current);
  });

  // 3) Cluster chips. Each chip writes/clears its community id in `filterSets`
  //    (declared above so the initial `applyMobileMode` can already read it).
  document.querySelectorAll<HTMLButtonElement>("[data-cs-cluster-chip]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const mode = btn.closest<HTMLElement>("[data-cs-mobile-mode]")?.getAttribute("data-cs-mobile-mode") as Mode | null;
      if (!mode) return;
      const cid = Number(btn.getAttribute("data-com"));
      if (Number.isNaN(cid)) {
        filterSets[mode].clear();
      } else {
        if (filterSets[mode].has(cid)) filterSets[mode].delete(cid);
        else filterSets[mode].add(cid);
      }
      // Reflect pressed state on chips of this mode.
      const chips = document.querySelectorAll<HTMLButtonElement>(`[data-cs-mobile-mode="${mode}"] [data-cs-cluster-chip]`);
      chips.forEach((c) => {
        const ccid = c.getAttribute("data-com");
        const isAll = ccid === "" || ccid == null;
        const set = filterSets[mode];
        const active = isAll ? set.size === 0 : set.has(Number(ccid));
        c.setAttribute("aria-pressed", active ? "true" : "false");
      });
      applyMobileFilters(mode);
    });
  });

  function currentMobileMode(): Mode | null {
    for (const m of allModes) {
      const section = document.querySelector<HTMLElement>(`[data-cs-mobile-mode="${m}"]`);
      if (section && !section.hidden) return m;
    }
    return null;
  }

  function applyMobileFilters(mode: Mode): void {
    const q = (mobileSearch?.value ?? "").trim().toLowerCase();
    const filter = filterSets[mode];
    const section = document.querySelector<HTMLElement>(`[data-cs-mobile-mode="${mode}"]`);
    if (!section) return;
    const groups = section.querySelectorAll<HTMLElement>("[data-cs-group]");
    let totalShown = 0;
    groups.forEach((group) => {
      const cid = Number(group.getAttribute("data-com"));
      const groupAllowed = filter.size === 0 || filter.has(cid);
      let groupShown = 0;
      const rows = group.querySelectorAll<HTMLElement>("[data-cs-row]");
      rows.forEach((row) => {
        if (!groupAllowed) { row.hidden = true; return; }
        const hay = (row.getAttribute("data-search") ?? "").toLowerCase();
        const ok = !q || hay.includes(q);
        row.hidden = !ok;
        if (ok) groupShown++;
      });
      group.hidden = !groupAllowed || groupShown === 0;
      totalShown += groupAllowed ? groupShown : 0;
    });
    const empty = section.querySelector<HTMLElement>("[data-cs-empty]");
    if (empty) empty.hidden = totalShown > 0;
  }
}
