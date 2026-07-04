// The only site code that talks to Umami. Components emit markup; this binds to
// it. Anchors carry `data-track-*` (handled here) instead of Umami's own
// `data-umami-event`, whose click handler re-navigates via location.href and so
// strips `download`; buttons use `data-umami-event` directly. Vocabulary and the
// reports it feeds live in docs/analytics.md.

export const ANALYTICS_EVENT_NAMES = [
  "work-download",    // a work's file link (pdf / epub / md / txt / docx)
  "archive-download", // bulk corpus archive on the downloads page
  "work-read",        // end of a work body seen after the dwell floor
  "work-progress",    // scrolled-to reading depth milestone (25/50/75)
  "video-play",       // embedded player opened
  "video-watch",      // outbound watch link to the hosting platform
  "channel-open",     // outbound author channel (telegram, youtube, email)
  "language-switch",  // header locale switcher
  "feed-subscribe",   // RSS feed link
  "search",           // site search completed for a query
  "share",            // share button
  "support-copy",     // support page: copy a channel value
  "support-open",     // support page: open a link channel
  "not-found",        // 404 page rendered
] as const;

export type AnalyticsEventName = (typeof ANALYTICS_EVENT_NAMES)[number];

type EventData = Record<string, string | number | boolean>;

/** The subset carried on anchors, tracked by the delegated click listener. */
export const LINK_EVENT_NAMES: ReadonlySet<AnalyticsEventName> = new Set([
  "work-download",
  "archive-download",
  "video-watch",
  "channel-open",
  "language-switch",
  "feed-subscribe",
  "support-open",
]);

type UmamiTracker = {
  track: (eventName: string, data?: EventData) => unknown;
};

declare global {
  interface Window {
    umami?: UmamiTracker;
  }
}

/** No-op until the tracker script has loaded (or forever, if it's blocked). */
function trackEvent(eventName: AnalyticsEventName, data: EventData = {}): void {
  window.umami?.track(eventName, data);
}

/** `data-track-format="pdf"` → `{ format: "pdf" }`; the event name and the bare read marker are not data. */
export function trackAttributeData(dataset: Record<string, string | undefined>): EventData {
  const data: EventData = {};
  for (const [key, value] of Object.entries(dataset)) {
    if (!key.startsWith("track") || key === "trackEvent" || key === "trackRead" || value === undefined) continue;
    const prop = key.slice("track".length);
    data[prop.charAt(0).toLowerCase() + prop.slice(1)] = value;
  }
  return data;
}

function isLinkEventName(value: string): value is AnalyticsEventName {
  return (LINK_EVENT_NAMES as ReadonlySet<string>).has(value);
}

function bindLinkEvents(): void {
  document.addEventListener(
    "click",
    event => {
      if (!(event.target instanceof Element)) return;
      const link = event.target.closest<HTMLAnchorElement>("a[data-track-event]");
      const eventName = link?.dataset.trackEvent;
      if (!link || eventName === undefined || !isLinkEventName(eventName)) return;
      // Don't preventDefault — Umami's beacon uses fetch keepalive, so it
      // outlives the navigation this click is about to start.
      trackEvent(eventName, trackAttributeData(link.dataset));
    },
    { capture: true },
  );
}

const READ_DWELL_MS = 30_000;
const READ_END_SLACK_PX = 96;
const PROGRESS_MARKS = [0.25, 0.5, 0.75] as const;

/** Milestones in (from, to] — the depths crossed by scrolling from `from` to `to`. */
export function progressMarksBetween(from: number, to: number): string[] {
  return PROGRESS_MARKS.filter(mark => from < mark && to >= mark).map(mark => String(mark * 100));
}

function bindReadTracking(): void {
  const body = document.querySelector<HTMLElement>("[data-track-read]");
  if (!body) return;
  const data = trackAttributeData(body.dataset);

  const visibleRatio = (rect: DOMRect): number =>
    rect.height <= 0 ? 1 : Math.min(1, Math.max(0, (window.innerHeight - rect.top) / rect.height));

  let dwelled = false;
  let endSeen = false;
  let done = false;
  let scheduled = false;
  // Seed at the landing depth so whatever's already on screen (short works,
  // anchor jumps) doesn't count as scrolled-to progress.
  let maxRatio = visibleRatio(body.getBoundingClientRect());

  const fire = (): void => {
    if (done || !dwelled || !endSeen || document.hidden) return; // a timer firing in a background tab isn't reading
    done = true;
    window.removeEventListener("scroll", onScroll);
    window.removeEventListener("resize", onScroll);
    document.removeEventListener("visibilitychange", fire);
    trackEvent("work-read", data);
  };

  const check = (): void => {
    const rect = body.getBoundingClientRect();
    const ratio = visibleRatio(rect);
    for (const progress of progressMarksBetween(maxRatio, ratio)) {
      trackEvent("work-progress", { ...data, progress });
    }
    if (ratio > maxRatio) maxRatio = ratio;
    if (!endSeen && rect.bottom <= window.innerHeight + READ_END_SLACK_PX) endSeen = true;
    fire();
  };

  const onScroll = (): void => {
    if (done || scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      check();
    });
  };

  window.addEventListener("scroll", onScroll, { passive: true });
  window.addEventListener("resize", onScroll, { passive: true });
  document.addEventListener("visibilitychange", fire);
  window.setTimeout(() => {
    dwelled = true;
    fire();
  }, READ_DWELL_MS);
  check();
}

function bindNotFound(): void {
  if (!document.querySelector("[data-track-not-found]")) return;
  trackEvent("not-found", { path: location.pathname });
}

const SEARCH_SETTLE_MS = 800;
const SEARCH_QUERY_MAX = 120;

function bindSearchTracking(): void {
  const results = document.querySelector<HTMLElement>("#pfsResults");
  if (!results) return;

  // Pagefind stamps the finished query and its match count on the results list.
  // Reading that (not the DOM row count) gives the true total and can't race
  // the lazy Pagefind import. Settle so a burst of typing logs one search.
  let timer: number | undefined;
  let lastQuery = "";
  const observer = new MutationObserver(() => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => {
      const query = (results.dataset.searchQuery ?? "").slice(0, SEARCH_QUERY_MAX);
      const total = Number(results.dataset.searchResults);
      if (!query || query === lastQuery || !Number.isFinite(total)) return;
      lastQuery = query;
      trackEvent("search", { query, results: total, locale: document.documentElement.lang });
    }, SEARCH_SETTLE_MS);
  });
  observer.observe(results, { attributes: true, attributeFilter: ["data-search-query", "data-search-results"] });
}

export function bindAnalytics(): void {
  bindLinkEvents();
  bindReadTracking();
  bindNotFound();
  bindSearchTracking();
}
