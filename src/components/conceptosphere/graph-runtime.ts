// Sigma 3 + graphology runtime for /conceptosphere/.
//
// Boot owns only the cross-surface orchestration: read the page JSON island,
// wire the mobile fallback, and mount/unmount the desktop Sigma runtime at
// the desktop breakpoint. Graph data, sessions, painters, layout, and
// interactions are explicit modules beside this file.

import {
  disposeDesktopRuntime,
  startDesktopRuntime,
  type DesktopRuntime,
} from "./desktop-runtime.ts";
import { wireMobile, type MobileController } from "./mobile-controller.ts";
import { GRAPH_DOM_IDS } from "./graph-dom.ts";
import { readPageConfig } from "./page-config.ts";
import type { ConceptosphereMode } from "./graph-types.ts";

declare global {
  interface Window {
    __csBootCleanup?: () => void;
  }
}

const DESKTOP_MEDIA_QUERY = "(min-width: 840px)";

export function bootGraph(): void {
  window.__csBootCleanup?.();

  const cfg = readPageConfig();
  if (!cfg) {
    console.error("conceptosphere: missing #cs-config payload");
    return;
  }

  const initialMode = modeFromHash() ?? cfg.initialMode;
  const runtimeCfg = { ...cfg, initialMode };
  const state = {
    currentMode: initialMode,
    desktopRuntime: null as DesktopRuntime | null,
    mobileController: null as MobileController | null,
    startSerial: 0,
    disposed: false,
  };
  const desktopMedia = window.matchMedia(DESKTOP_MEDIA_QUERY);

  const setSharedMode = (mode: ConceptosphereMode) => {
    if (state.disposed) return;
    state.currentMode = mode;
    reflectDocumentMode(mode);
    writeModeHash(mode);
    state.mobileController?.setMode(mode);
  };

  reflectDocumentMode(state.currentMode);
  state.mobileController = wireMobile(runtimeCfg, setSharedMode);

  const mountDesktop = () => {
    if (state.disposed || state.desktopRuntime) return;
    const serial = ++state.startSerial;
    void startDesktopRuntime(runtimeCfg, state.currentMode, setSharedMode)
      .then((runtime) => {
        if (state.disposed || serial !== state.startSerial || !desktopMedia.matches) {
          disposeDesktopRuntime(runtime);
          return;
        }
        state.desktopRuntime = runtime;
      })
      .catch((err) => console.error(err));
  };

  const unmountDesktop = () => {
    state.startSerial++;
    if (!state.desktopRuntime) return;
    disposeDesktopRuntime(state.desktopRuntime);
    state.desktopRuntime = null;
  };

  if (desktopMedia.matches) mountDesktop();

  const onDesktopMediaChange = (event: MediaQueryListEvent) => {
    if (event.matches) mountDesktop();
    else unmountDesktop();
  };
  desktopMedia.addEventListener("change", onDesktopMediaChange);

  const onHashChange = () => {
    const mode = modeFromHash();
    if (!mode || mode === state.currentMode || state.disposed) return;
    state.currentMode = mode;
    reflectDocumentMode(mode);
    state.mobileController?.setMode(mode);
    void state.desktopRuntime?.setMode(mode).catch((err) => console.error(err));
  };
  window.addEventListener("hashchange", onHashChange);

  window.__csBootCleanup = () => {
    state.disposed = true;
    state.startSerial++;
    desktopMedia.removeEventListener("change", onDesktopMediaChange);
    window.removeEventListener("hashchange", onHashChange);
    state.mobileController?.dispose();
    state.mobileController = null;
    unmountDesktop();
    if (window.__csBootCleanup) delete window.__csBootCleanup;
  };
}

function modeFromHash(): ConceptosphereMode | null {
  const raw = window.location.hash.replace(/^#/, "").trim().toLowerCase();
  if (raw === "book") return "books";
  if (raw === "concept") return "concepts";
  return raw === "books" || raw === "concepts" ? raw : null;
}

function writeModeHash(mode: ConceptosphereMode): void {
  if (modeFromHash() === mode) return;
  const url = new URL(window.location.href);
  url.hash = mode;
  window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
}

function reflectDocumentMode(mode: ConceptosphereMode): void {
  document.getElementById(GRAPH_DOM_IDS.stageShell)?.setAttribute("data-cs-mode", mode);
}
