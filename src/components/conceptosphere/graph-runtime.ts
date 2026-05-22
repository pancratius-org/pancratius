// Sigma 3 + graphology runtime for /conceptosphere/.
//
// This module owns desktop graph orchestration: data load, Sigma mounting,
// hover/click/legend/search wiring, and the analytical side panel. Layout,
// theme math, hull rendering, payload parsing, panel rendering, and mobile
// list wiring live in small focused modules beside it.
//
// The Astro shell writes a small `#cs-config` JSON island (graph URLs,
// initial mode, counts, slug→book pair info, cover URLs, localized strings)
// before this module loads. We read it once at boot so the runtime stays a
// consumer of page configuration rather than a second localization layer.

import type Graph from "graphology";
import Sigma from "sigma";
import type { Settings as SigmaSettings } from "sigma/settings";
import type { NodeDisplayData, EdgeDisplayData } from "sigma/types";

import { parseGraphPayload } from "./graph-payload";
import { buildGraphModel } from "./graph-layout";
import { edgeVisual, readGraphTheme, type GraphTheme } from "./graph-theme";
import { drawHulls } from "./hull-render";
import { wireMobile, type MobileController } from "./mobile-controller";
import { hidePanel, showPanel } from "./panel-render";
import type {
  ConceptosphereMode as Mode,
  GraphPayload,
  NodeJson,
  PageConfig,
} from "./runtime-types";

// ─────────────────────────────────────────────────────────────────────
// Window contract written by the Astro shell.
//
// The route owns the locale and the strings; this runtime is a pure consumer.
// It never imports a locale dictionary or `i18n` — everything reader-facing
// arrives in `cfg.strings`.
// ─────────────────────────────────────────────────────────────────────

declare global {
  interface Window {
    __csRenderer?: Sigma;
    __csGraph?:    Graph;
    __csBootCleanup?: () => void;
  }
}

const DESKTOP_MEDIA_QUERY = "(min-width: 840px)";
const MOBILE_MEDIA_QUERY = "(max-width: 839px)";

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
// Small helpers.
// ─────────────────────────────────────────────────────────────────────

function escapeHtml(s: string): string {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;",
  }[c]!));
}

// ─────────────────────────────────────────────────────────────────────
// Per-session state.
// ─────────────────────────────────────────────────────────────────────

interface Session {
  mode:     Mode;
  data:     GraphPayload;
  graph:    Graph;
  renderer: Sigma;
  theme:    GraphTheme;
  nodesByCom: Map<number, NodeJson[]>;
  comColor: Map<number, string>;
  comLabel: Map<number, string>;
  comSize:  Map<number, number>;
  comRgb:   Map<number, [number, number, number]>;
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
  window.__csBootCleanup?.();
  const cfg = readPageConfig();
  if (!cfg) {
    // Page shell missed its hook — surface as a console error and bail. The
    // mobile fallback still renders without us, so this is best-effort.
    console.error("conceptosphere: missing #cs-config payload");
    return;
  }

  let currentMode: Mode = cfg.initialMode;
  let desktopCtx: AppContext | null = null;
  let mobileController: MobileController | null = null;
  let startSerial = 0;
  let disposed = false;
  const desktopMedia = window.matchMedia(DESKTOP_MEDIA_QUERY);

  const setSharedMode = (mode: Mode) => {
    if (disposed) return;
    currentMode = mode;
    mobileController?.setMode(mode);
  };
  mobileController = wireMobile(cfg, setSharedMode);

  const mountDesktop = () => {
    if (disposed || desktopCtx) return;
    const serial = ++startSerial;
    void start(cfg, currentMode, setSharedMode)
      .then((ctx) => {
        if (disposed || serial !== startSerial || !desktopMedia.matches) {
          disposeDesktop(ctx);
          return;
        }
        desktopCtx = ctx;
      })
      .catch((err) => console.error(err));
  };

  const unmountDesktop = () => {
    startSerial++;
    if (!desktopCtx) return;
    disposeDesktop(desktopCtx);
    desktopCtx = null;
  };

  if (desktopMedia.matches) mountDesktop();
  const onDesktopMediaChange = (event: MediaQueryListEvent) => {
    if (event.matches) mountDesktop();
    else unmountDesktop();
  };
  desktopMedia.addEventListener("change", onDesktopMediaChange);

  window.__csBootCleanup = () => {
    disposed = true;
    startSerial++;
    desktopMedia.removeEventListener("change", onDesktopMediaChange);
    mobileController?.dispose();
    mobileController = null;
    unmountDesktop();
    if (window.__csBootCleanup) delete window.__csBootCleanup;
  };
}

async function start(
  cfg: PageConfig,
  initialMode: Mode,
  onModeChange: (mode: Mode) => void,
): Promise<AppContext> {
  const stage = document.getElementById("cs-graph");
  const hulls = document.getElementById("cs-hulls") as unknown as SVGSVGElement | null;
  if (!stage || !hulls) throw new Error("conceptosphere: missing desktop graph mount");

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
    disposers: [],
  };

  const onPanelClose = () => {
    if (!ctx.session) return;
    ctx.session.state.pinned = null;
    ctx.session.state.hovered = null;
    ctx.session.applyHighlight();
    hidePanel(ctx);
  };
  ctx.panelClose.addEventListener("click", onPanelClose);
  ctx.disposers.push(() => ctx.panelClose.removeEventListener("click", onPanelClose));

  const onKeydown = (e: KeyboardEvent) => {
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
  };
  document.addEventListener("keydown", onKeydown);
  ctx.disposers.push(() => document.removeEventListener("keydown", onKeydown));

  const onResize = () => {
    ctx.session?.scheduleHullPaint();
  };
  window.addEventListener("resize", onResize);
  ctx.disposers.push(() => window.removeEventListener("resize", onResize));

  document.querySelectorAll<HTMLButtonElement>(".cs-modebar [data-cs-mode-toggle] button[data-mode]").forEach((btn) => {
    const onClick = () => {
      const mode = btn.getAttribute("data-mode") as Mode | null;
      if (!mode) return;
      onModeChange(mode);
      void setMode(ctx, mode).catch((err) => showGraphError(ctx, err));
    };
    btn.addEventListener("click", onClick);
    ctx.disposers.push(() => btn.removeEventListener("click", onClick));
  });

  let themeRaf: number | null = null;
  const themeObserver = new MutationObserver(() => {
    if (themeRaf !== null) cancelAnimationFrame(themeRaf);
    themeRaf = requestAnimationFrame(() => {
      themeRaf = null;
      if (ctx.session) {
        rethemeSession(ctx, ctx.session);
      }
    });
  });
  themeObserver.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["data-theme"],
  });
  ctx.disposers.push(() => {
    if (themeRaf !== null) cancelAnimationFrame(themeRaf);
    themeObserver.disconnect();
  });

  try {
    await setMode(ctx, initialMode);
  } catch (err) {
    showGraphError(ctx, err);
  }
  return ctx;
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
  disposers:     (() => void)[];
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
  setModeButtons(document.querySelector(".cs-modebar") ?? document, mode);

  // Refresh chrome copy + footer caption.
  const strings = ctx.cfg.strings;
  const modeCopy = strings.modes[mode];
  setText("cs-page-h1",   modeCopy.h1);
  setText("cs-page-lede", modeCopy.lede);
  setText("cs-page-meth", modeCopy.meth);
  setText("cs-counts", ctx.cfg.modeCounts[mode]);
  setText("cs-legend-title", strings.legendTitle);

  // Desktop graph only — early bail when we're on a phone.
  if (window.matchMedia(MOBILE_MEDIA_QUERY).matches) {
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

function setModeButtons(root: ParentNode, mode: Mode): void {
  root.querySelectorAll<HTMLButtonElement>("[data-cs-mode-toggle] button[data-mode]").forEach((button) => {
    const isActive = button.getAttribute("data-mode") === mode;
    button.setAttribute("aria-pressed", isActive ? "true" : "false");
  });
}

async function fetchJson(ctx: AppContext, url: string): Promise<GraphPayload> {
  const cached = ctx.dataCache.get(url);
  if (cached) return cached;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} (${res.status})`);
  const data = parseGraphPayload(await res.json(), url);
  applyLocalizedBookTitles(ctx, data);
  ctx.dataCache.set(url, data);
  return data;
}

/**
 * Replace RU book titles in a freshly-fetched payload with the locale's
 * authored titles. The graph JSON only carries RU; the page passes the
 * localised title map in `cfg.bookSlugInfo`. Applied to top-level book nodes,
 * concept `top_books`, and each book node's `top_similar*` neighbour titles so
 * panel rendering and label painting see one consistent title per slug.
 */
function applyLocalizedBookTitles(ctx: AppContext, data: GraphPayload): void {
  const info = ctx.cfg.bookSlugInfo;
  if (!info) return;
  const titleFor = (slug: string | undefined): string | null =>
    slug && info[slug]?.title ? info[slug].title : null;
  for (const n of data.nodes) {
    const t = titleFor(n.slug ?? n.id);
    if (t) n.title = t;
    for (const book of n.top_books ?? []) {
      const bt = titleFor(book.slug);
      if (bt) book.title = bt;
    }
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
  clearDebugSession(s);
  ctx.session = null;
  ctx.stage.replaceChildren();
  // Also clear the hull overlay so old paths don't linger on swap.
  while (ctx.hulls.firstChild) ctx.hulls.removeChild(ctx.hulls.firstChild);
}

function disposeDesktop(ctx: AppContext): void {
  ctx.loadSerial++;
  destroySession(ctx);
  for (const dispose of ctx.disposers.splice(0)) {
    dispose();
  }
  ctx.legendList.innerHTML = "";
  ctx.legendClear.hidden = true;
  ctx.panelBody.innerHTML = "";
  ctx.panel.classList.remove("is-open", "is-pinned");
  ctx.stage.replaceChildren();
  while (ctx.hulls.firstChild) ctx.hulls.removeChild(ctx.hulls.firstChild);
}

function exposeDebugSession(renderer: Sigma, graph: Graph): void {
  if (!import.meta.env.DEV) return;
  window.__csRenderer = renderer;
  window.__csGraph = graph;
}

function clearDebugSession(s: Session): void {
  if (!import.meta.env.DEV) return;
  if (window.__csRenderer === s.renderer) delete window.__csRenderer;
  if (window.__csGraph === s.graph) delete window.__csGraph;
}

function showGraphError(ctx: AppContext, err: unknown): void {
  console.error(err);
  destroySession(ctx);
  const msg = escapeHtml(err instanceof Error ? err.message : String(err));
  ctx.stage.innerHTML =
    `<p class="cs-load-error">${escapeHtml(ctx.cfg.strings.loadErrorPrefix)}: ${msg}</p>`;
}

function rethemeSession(ctx: AppContext, s: Session): void {
  Object.assign(s.theme, readGraphTheme());
  s.renderer.setSetting("defaultEdgeColor", s.theme.defaultEdgeColor);
  s.renderer.setSetting("labelColor", { color: s.theme.labelColor });

  s.graph.forEachEdge((edge, attrs, _source, _target, sourceAttrs, targetAttrs) => {
    const sc = sourceAttrs.community as number;
    const tc = targetAttrs.community as number;
    const ca = s.comRgb.get(sc);
    const cb = s.comRgb.get(tc);
    if (!ca || !cb) return;
    const t = typeof attrs._tone === "number" ? attrs._tone : 0;
    const visual = edgeVisual(s.theme, s.mode, Boolean(attrs.within), t, ca, cb);
    s.graph.setEdgeAttribute(edge, "color", visual.color);
    s.graph.setEdgeAttribute(edge, "_color", visual.color);
    s.graph.setEdgeAttribute(edge, "size", visual.size);
    s.graph.setEdgeAttribute(edge, "_size", visual.size);
  });

  s.applyHighlight();
  s.scheduleHullPaint();
  if (s.state.pinned) {
    showPanel(ctx, s, s.state.pinned, true);
  } else if (s.state.hovered) {
    showPanel(ctx, s, s.state.hovered, false);
  }
}

// ─────────────────────────────────────────────────────────────────────
// Build a session (Sigma renderer + canvas painters around a graph model).
// ─────────────────────────────────────────────────────────────────────

function buildSession(ctx: AppContext, data: GraphPayload, mode: Mode): Session {
  const theme = readGraphTheme();

  const state = {
    hovered: null as string | null,
    pinned:  null as string | null,
    filterComs: new Set<number>(),
    search: "",
  };

  const model = buildGraphModel(data, mode, theme, ctx.reduceMotion);
  const { graph, nodesByCom, comColor, comLabel, comSize, comRgb } = model;

  // ── Sigma renderer ──────────────────────────────────────────────
  let rendererRef: Sigma | null = null;
  const settings: Partial<SigmaSettings> = {
    renderEdgeLabels: false,
    defaultEdgeColor: theme.defaultEdgeColor,
    labelColor: { color: theme.labelColor },
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
      const labelScale = 1 + zoomLift * (theme.isLight ? 0.46 : 0.32);
      const labelSize = baseLabelSize * labelScale;
      const font = drawSettings.labelFont;
      const weight = drawSettings.labelWeight;
      context.font = `${weight} ${labelSize}px ${font}`;
      const label = displayData.label;
      const x = displayData.x + displayData.size + 3 + zoomLift * 2;
      const y = displayData.y + labelSize / 3;
      context.lineWidth = (theme.isLight ? 3.4 : 2.5) * Math.min(1.24, labelScale);
      context.strokeStyle = theme.labelHalo;
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
      if (dd._dim || dd.color === theme.dimNode) return;
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
      c2d.strokeStyle = theme.badgeHalo;
      c2d.fillStyle = theme.badgeInk;
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
      c2d.strokeStyle = strong ? theme.focusRing : theme.focusRingMuted;
      c2d.lineWidth = innerWidth;
      c2d.stroke();

      c2d.beginPath();
      c2d.arc(vp.x, vp.y, r + outerGap, 0, Math.PI * 2);
      c2d.strokeStyle = strong ? theme.focusRingSoft : theme.focusRingMutedSoft;
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

      c2d.fillStyle = theme.calloutBg;
      c2d.strokeStyle = strong ? theme.focusCalloutBorder : theme.focusCalloutBorderMuted;
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

      c2d.fillStyle = theme.calloutInk;
      c2d.textBaseline = "middle";
      c2d.fillText(drawn, boxX + padX, boxY + boxH / 2 + 0.5);
    }
    c2d.restore();
  }
  renderer.on("afterRender", paintFocusDecoration);

  exposeDebugSession(renderer, graph);

  renderer.getCamera().setState({
    x: 0.5,
    y: 0.5,
    angle: 0,
    ratio: initialCameraRatio(ctx.stage, mode),
  });

  const session: Session = {
    mode, data, graph, renderer, theme, nodesByCom,
    comColor, comLabel, comSize, comRgb,
    state,
    applyHighlight: () => { /* set below */ },
    scheduleHullPaint: () => { /* set below */ },
    disposed: false,
  };
  return session;
}

function initialCameraRatio(stage: HTMLElement, mode: Mode): number {
  const box = stage.getBoundingClientRect();
  const aspect = box.height > 0 ? box.width / box.height : 1.6;
  let ratio = mode === "books" ? 1.25 : 1.08;
  if (aspect < 1.35) ratio += 0.10;
  if (aspect > 1.85) ratio -= 0.03;
  return Math.max(1.02, Math.min(1.34, ratio));
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
    if (s.mode === "concepts") {
      const books = (attrs.top_books as { title?: string; slug?: string }[] | undefined) ?? [];
      for (const book of books) {
        if ((book.title ?? "").toLowerCase().includes(q)) return true;
        if ((book.slug ?? "").toLowerCase().includes(q)) return true;
      }
    }
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
        out.color = s.theme.dimNode;
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
        out.color = s.theme.dimEdge;
        out.size = (data._size as number) * 0.5;
        out.hidden = false;
      } else if (hasFocus) {
        out.color = s.theme.focusEdge;
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
      p.setAttribute("fill-opacity", highlight ? s.theme.hullFillOpacity : s.theme.hullDimFillOpacity);
      p.setAttribute("stroke-opacity", highlight ? s.theme.hullStrokeOpacity : s.theme.hullDimStrokeOpacity);
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
