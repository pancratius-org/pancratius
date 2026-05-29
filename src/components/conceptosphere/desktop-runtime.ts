import { GraphDataStore } from "./graph-data.ts";
import { GRAPH_DOM_IDS, GRAPH_DOM_SELECTORS } from "./graph-dom.ts";
import {
  bindGraphInteractions,
  clearGraphInteractionState,
  refreshOpenPanel,
  type GraphInteractionBindings,
  type GraphInteractionHost,
} from "./graph-interactions.ts";
import {
  createGraphSession,
  disposeGraphSession,
  rethemeGraphSession,
  type GraphSession,
} from "./graph-session.ts";
import { hidePanel } from "./panel-render.ts";
import { isConceptosphereMode, type ConceptosphereMode } from "./graph-types.ts";
import type { PageConfig } from "./page-config.ts";

export interface DesktopRuntime {
  dispose: () => void;
}

interface DesktopHost extends GraphInteractionHost {
  stage: HTMLElement;
  hulls: SVGSVGElement;
  panelClose: HTMLElement;
  legendTitle: HTMLElement;
  reduceMotion: boolean;
}

export async function startDesktopRuntime(
  cfg: PageConfig,
  initialMode: ConceptosphereMode,
  onModeChange: (mode: ConceptosphereMode) => void,
): Promise<DesktopRuntime> {
  const runtime = new DesktopGraphRuntime(cfg, onModeChange);
  await runtime.start(initialMode);
  return runtime;
}

export function disposeDesktopRuntime(runtime: DesktopRuntime): void {
  runtime.dispose();
}

class DesktopGraphRuntime implements DesktopRuntime {
  private readonly host: DesktopHost;
  private readonly dataStore: GraphDataStore;
  private readonly disposers: (() => void)[] = [];
  private session: GraphSession | null = null;
  private bindings: GraphInteractionBindings | null = null;
  private loadSerial = 0;
  private disposed = false;

  constructor(
    cfg: PageConfig,
    private readonly onModeChange: (mode: ConceptosphereMode) => void,
  ) {
    this.host = createDesktopHost(cfg);
    this.dataStore = new GraphDataStore(cfg);
  }

  async start(initialMode: ConceptosphereMode): Promise<void> {
    this.installControls();
    try {
      await this.setMode(initialMode);
    } catch (err) {
      this.showGraphError(err);
    }
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.loadSerial++;
    this.destroySession();
    for (const dispose of this.disposers.splice(0)) dispose();
    this.clearSurfaces();
  }

  private installControls(): void {
    const closePanel = () => this.closePanel();
    this.host.panelClose.addEventListener("click", closePanel);
    this.disposers.push(() => this.host.panelClose.removeEventListener("click", closePanel));

    const onKeydown = (event: KeyboardEvent) => this.handleKeydown(event);
    document.addEventListener("keydown", onKeydown);
    this.disposers.push(() => document.removeEventListener("keydown", onKeydown));

    const onResize = () => this.session?.scheduleHullPaint();
    window.addEventListener("resize", onResize);
    this.disposers.push(() => window.removeEventListener("resize", onResize));

    document.querySelectorAll<HTMLButtonElement>(GRAPH_DOM_SELECTORS.desktopModeButtons).forEach((button) => {
      const onClick = () => {
        const mode = modeFromButton(button);
        if (!mode) return;
        this.onModeChange(mode);
        void this.setMode(mode).catch((err) => this.showGraphError(err));
      };
      button.addEventListener("click", onClick);
      this.disposers.push(() => button.removeEventListener("click", onClick));
    });

    this.installThemeObserver();
  }

  private installThemeObserver(): void {
    let themeRaf: number | null = null;
    const observer = new MutationObserver(() => {
      if (themeRaf !== null) cancelAnimationFrame(themeRaf);
      themeRaf = requestAnimationFrame(() => {
        themeRaf = null;
        const session = this.session;
        if (!session) return;
        rethemeGraphSession(session);
        refreshOpenPanel(this.host, session);
      });
    });
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    this.disposers.push(() => {
      if (themeRaf !== null) cancelAnimationFrame(themeRaf);
      observer.disconnect();
    });
  }

  private handleKeydown(event: KeyboardEvent): void {
    if (isSearchKeyboardShortcut(event) || isSlashSearchShortcut(event)) {
      event.preventDefault();
      this.focusSearch();
      return;
    }

    if (!isEscapeDismissal(event)) return;
    if (document.activeElement === this.host.desktopSearch) this.host.desktopSearch.blur();
    const session = this.session;
    if (!session) return;
    clearGraphInteractionState(this.host, session);
  }

  private focusSearch(): void {
    this.host.desktopSearch.focus();
    this.host.desktopSearch.select();
  }

  private async setMode(mode: ConceptosphereMode): Promise<void> {
    if (this.disposed) return;
    this.renderChrome(mode);
    this.host.desktopSearch.placeholder = this.host.cfg.strings.modes[mode].searchPlaceholder;
    this.host.desktopSearch.value = "";

    const serial = ++this.loadSerial;
    this.destroySession();
    this.clearSurfaces();

    const data = await this.dataStore.load(mode);
    if (this.isStaleLoad(serial)) return;

    const next = createGraphSession(this.host, data);
    if (this.isStaleLoad(serial)) {
      disposeGraphSession(next);
      this.host.stage.replaceChildren();
      return;
    }

    this.session = next;
    this.bindings = bindGraphInteractions(this.host, next);
    next.refresh();
  }

  private renderChrome(mode: ConceptosphereMode): void {
    const strings = this.host.cfg.strings;
    const modeCopy = strings.modes[mode];
    setModeButtons(document.querySelector(GRAPH_DOM_SELECTORS.modeToggleRoot) ?? document, mode);
    setText(GRAPH_DOM_IDS.pageHeading, modeCopy.h1);
    setText(GRAPH_DOM_IDS.pageLede, modeCopy.lede);
    setText(GRAPH_DOM_IDS.pageMethod, modeCopy.meth);
    setText(GRAPH_DOM_IDS.counts, this.host.cfg.modeCounts[mode]);
    this.host.legendTitle.textContent = strings.legendTitle;
  }

  private isStaleLoad(serial: number): boolean {
    return this.disposed || serial !== this.loadSerial;
  }

  private closePanel(): void {
    const session = this.session;
    if (!session) return;
    session.interactions.clearFocus();
    session.refresh();
    hidePanel(this.host);
  }

  private destroySession(): void {
    const session = this.session;
    this.bindings?.dispose();
    this.bindings = null;
    if (!session) return;
    disposeGraphSession(session);
    this.session = null;
    this.host.stage.replaceChildren();
    clearSvg(this.host.hulls);
  }

  private clearSurfaces(): void {
    this.host.legendList.replaceChildren();
    this.host.legendClear.hidden = true;
    this.host.panelBody.replaceChildren();
    this.host.panel.classList.remove("is-open", "is-pinned");
    this.host.stage.replaceChildren();
    clearSvg(this.host.hulls);
  }

  private showGraphError(err: unknown): void {
    console.error(err);
    this.destroySession();
    const error = document.createElement("p");
    error.className = "cs-load-error";
    error.textContent = `${this.host.cfg.strings.loadErrorPrefix}: ${err instanceof Error ? err.message : String(err)}`;
    this.host.stage.replaceChildren(error);
  }
}

function createDesktopHost(cfg: PageConfig): DesktopHost {
  return {
    cfg,
    stage: requireElement(GRAPH_DOM_IDS.stage, HTMLElement),
    hulls: requireElement(GRAPH_DOM_IDS.hulls, SVGSVGElement),
    panel: requireElement(GRAPH_DOM_IDS.panel, HTMLElement),
    panelBody: requireElement(GRAPH_DOM_IDS.panelBody, HTMLElement),
    panelClose: requireElement(GRAPH_DOM_IDS.panelClose, HTMLElement),
    desktopSearch: requireElement(GRAPH_DOM_IDS.desktopSearch, HTMLInputElement),
    legendList: requireElement(GRAPH_DOM_IDS.legendList, HTMLElement),
    legendClear: requireElement(GRAPH_DOM_IDS.legendClear, HTMLButtonElement),
    legendTitle: requireElement(GRAPH_DOM_IDS.legendTitle, HTMLElement),
    reduceMotion: window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  };
}

function requireElement<T extends Element>(id: string, ctor: { new(): T }): T {
  const el = document.getElementById(id);
  if (!(el instanceof ctor)) throw new Error(`conceptosphere: missing #${id}`);
  return el;
}

function setModeButtons(root: ParentNode, mode: ConceptosphereMode): void {
  root.querySelectorAll<HTMLButtonElement>(GRAPH_DOM_SELECTORS.modeButtons).forEach((button) => {
    const isActive = modeFromButton(button) === mode;
    button.setAttribute("aria-pressed", isActive ? "true" : "false");
  });
}

function modeFromButton(button: HTMLButtonElement): ConceptosphereMode | null {
  const mode = button.getAttribute("data-mode");
  return isConceptosphereMode(mode) ? mode : null;
}

function isSearchKeyboardShortcut(event: KeyboardEvent): boolean {
  return (event.metaKey || event.ctrlKey)
    && (event.key === "k" || event.key === "K")
    && !event.altKey;
}

function isSlashSearchShortcut(event: KeyboardEvent): boolean {
  return event.key === "/"
    && !event.metaKey
    && !event.ctrlKey
    && !event.altKey
    && !isEditableTarget(document.activeElement);
}

function isEscapeDismissal(event: KeyboardEvent): boolean {
  return event.key === "Escape";
}

function isEditableTarget(el: Element | null): boolean {
  if (!el) return false;
  return (el instanceof HTMLElement && el.isContentEditable)
    || el instanceof HTMLInputElement
    || el instanceof HTMLTextAreaElement
    || el instanceof HTMLSelectElement;
}

function setText(id: string, text: string): void {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function clearSvg(svg: SVGSVGElement): void {
  while (svg.firstChild) svg.removeChild(svg.firstChild);
}
