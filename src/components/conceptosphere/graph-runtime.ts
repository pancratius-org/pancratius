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

  let currentMode = cfg.initialMode;
  let desktopRuntime: DesktopRuntime | null = null;
  let mobileController: MobileController | null = null;
  let startSerial = 0;
  let disposed = false;
  const desktopMedia = window.matchMedia(DESKTOP_MEDIA_QUERY);

  const setSharedMode = (mode: ConceptosphereMode) => {
    if (disposed) return;
    currentMode = mode;
    mobileController?.setMode(mode);
  };

  mobileController = wireMobile(cfg, setSharedMode);

  const mountDesktop = () => {
    if (disposed || desktopRuntime) return;
    const serial = ++startSerial;
    void startDesktopRuntime(cfg, currentMode, setSharedMode)
      .then((runtime) => {
        if (disposed || serial !== startSerial || !desktopMedia.matches) {
          disposeDesktopRuntime(runtime);
          return;
        }
        desktopRuntime = runtime;
      })
      .catch((err) => console.error(err));
  };

  const unmountDesktop = () => {
    startSerial++;
    if (!desktopRuntime) return;
    disposeDesktopRuntime(desktopRuntime);
    desktopRuntime = null;
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
