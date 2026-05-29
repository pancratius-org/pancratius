import Sigma from "sigma";

import { applyGraphHighlighting } from "./graph-highlight.ts";
import { buildGraphModel, type ConceptGraph, type ConceptRenderer } from "./graph-model.ts";
import { edgeVisual, readGraphTheme, type GraphTheme } from "./graph-theme.ts";
import { initialCameraRatio, installCanvasPainters, sigmaSettingsFor } from "./graph-painters.ts";
import type { CommunityCatalog, GraphData } from "./graph-data.ts";
import type { HullLayer } from "./hull-render.ts";
import { createHullLayer } from "./hull-render.ts";
import { GraphInteractionState } from "./interaction-state.ts";

export interface GraphSessionHost {
  stage: HTMLElement;
  hulls: SVGSVGElement;
  reduceMotion: boolean;
}

declare global {
  interface Window {
    __csRenderer?: ConceptRenderer;
    __csGraph?: ConceptGraph;
  }
}

export class GraphSession {
  readonly mode: GraphData["mode"];
  readonly data: GraphData;
  readonly graph: ConceptGraph;
  readonly renderer: ConceptRenderer;
  readonly theme: GraphTheme;
  readonly communities: CommunityCatalog;
  readonly interactions = new GraphInteractionState();

  private readonly hullLayer: HullLayer;
  private disposed = false;

  constructor(host: GraphSessionHost, data: GraphData) {
    this.mode = data.mode;
    this.data = data;
    this.theme = readGraphTheme();

    const model = buildGraphModel(data, this.theme, host.reduceMotion);
    this.graph = model.graph;
    this.communities = model.communities;

    let rendererRef: ConceptRenderer | null = null;
    this.renderer = new Sigma(
      this.graph,
      host.stage,
      sigmaSettingsFor({ mode: this.mode, theme: this.theme, renderer: () => rendererRef }),
    );
    rendererRef = this.renderer;

    markCanvasesPresentational(this.renderer);
    installCanvasPainters({
      mode: this.mode,
      graph: this.graph,
      renderer: this.renderer,
      theme: this.theme,
      state: this.interactions,
    });
    exposeDebugSession(this.renderer, this.graph);

    this.renderer.getCamera().setState({
      x: 0.5,
      y: 0.5,
      angle: 0,
      ratio: initialCameraRatio(host.stage, this.mode),
    });

    this.hullLayer = createHullLayer({
      stage: host.stage,
      hulls: host.hulls,
      mode: this.mode,
      graph: this.graph,
      renderer: this.renderer,
      theme: this.theme,
      nodesByCommunity: this.communities.nodesByCommunity,
      isDisposed: () => this.disposed,
    });
  }

  refresh(): void {
    applyGraphHighlighting({
      mode: this.mode,
      graph: this.graph,
      renderer: this.renderer,
      theme: this.theme,
      hulls: this.hullLayer.element,
      state: this.interactions.snapshot(),
    });
  }

  scheduleHullPaint(): void {
    this.hullLayer.schedule();
  }

  retheme(): void {
    Object.assign(this.theme, readGraphTheme());
    this.renderer.setSetting("defaultEdgeColor", this.theme.defaultEdgeColor);
    this.renderer.setSetting("labelColor", { color: this.theme.labelColor });

    this.graph.forEachEdge((edge, attrs, _source, _target, sourceAttrs, targetAttrs) => {
      const sourceCommunity = this.communities.byId.get(sourceAttrs.communityId);
      const targetCommunity = this.communities.byId.get(targetAttrs.communityId);
      if (!sourceCommunity || !targetCommunity) return;
      const visual = edgeVisual(
        this.theme,
        this.mode,
        attrs.withinCommunity,
        attrs.tone,
        sourceCommunity.rgb,
        targetCommunity.rgb,
      );
      this.graph.setEdgeAttribute(edge, "color", visual.color);
      this.graph.setEdgeAttribute(edge, "baseColor", visual.color);
      this.graph.setEdgeAttribute(edge, "size", visual.size);
      this.graph.setEdgeAttribute(edge, "baseSize", visual.size);
    });

    this.refresh();
    this.scheduleHullPaint();
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.hullLayer.dispose();
    try {
      this.renderer.kill();
    } catch {
      // Sigma cleanup is best-effort during fast mode/viewport swaps.
    }
    clearDebugSession(this);
  }
}

export function createGraphSession(host: GraphSessionHost, data: GraphData): GraphSession {
  return new GraphSession(host, data);
}

export function disposeGraphSession(session: GraphSession): void {
  session.dispose();
}

export function rethemeGraphSession(session: GraphSession): void {
  session.retheme();
}

function markCanvasesPresentational(renderer: ConceptRenderer): void {
  for (const canvas of Object.values(renderer.getCanvases())) {
    if (canvas instanceof HTMLCanvasElement) {
      canvas.setAttribute("aria-hidden", "true");
      canvas.setAttribute("role", "presentation");
    }
  }
}

function exposeDebugSession(renderer: ConceptRenderer, graph: ConceptGraph): void {
  if (!import.meta.env.DEV) return;
  window.__csRenderer = renderer;
  window.__csGraph = graph;
}

function clearDebugSession(session: GraphSession): void {
  if (!import.meta.env.DEV) return;
  if (window.__csRenderer === session.renderer) delete window.__csRenderer;
  if (window.__csGraph === session.graph) delete window.__csGraph;
}
