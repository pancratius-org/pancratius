// Shared core for the visual diagnostics. Three different tools live on top of
// this module, and they are deliberately three different shapes:
//
//   * the regression GATE is a Playwright spec (tests/visual_audit.spec.ts) —
//     it asserts with expect(), so it lives with the other specs, not here;
//   * the snapshot GENERATORS (audit.ts, viewport.ts, shots.ts) are thin
//     capture scripts that write PNGs to .cache/visual-audit/ for human review;
//   * the Lighthouse REPORT (lighthouse.ts) shells the CLI and prints a table.
//
// What is genuinely shared sits here: the theme × viewport × route matrix, the
// theme seeding, gotoStable, screenshot naming, and the pure helpers. The pure
// helpers (parseTag, screenshotName, midScrollY, overflows, settleMsFor) are
// kept free of browser imports at the value level so they can be unit-tested
// without a browser — see harness.test.ts. They are also imported by the gate
// spec, which is why this file must stay importable from a Playwright spec.

import { chromium } from "@playwright/test";
import type { Browser, BrowserContext, Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";

export type Theme = "dark" | "light";
export type Viewport = { width: number; height: number };
export type NamedViewport = { name: string; viewport: Viewport };
export type Route = { name: string; path: string };

/** Base URL of the running site. Override with BASE_URL for a non-default port. */
export const BASE_URL = process.env.BASE_URL ?? "http://localhost:4321";

/** Disposable output root. Everything here is gitignored scratch. */
export const CACHE_ROOT = ".cache/visual-audit";

export const THEMES: readonly Theme[] = ["dark", "light"];

/** The standard desktop+mobile pair used by the audit and viewport scripts. */
export const DESKTOP_MOBILE: readonly NamedViewport[] = [
  { name: "desktop", viewport: { width: 1440, height: 900 } },
  { name: "mobile", viewport: { width: 375, height: 812 } },
];

/** The localStorage key the site's ThemeInit reads to pick a theme. */
export const THEME_STORAGE_KEY = "pncr-theme";

const CONCEPTOSPHERE_SETTLE_MS = 1200;

// --- the audit route set (shared by the gate spec and the audit generator) ---

/**
 * The surfaces the audit covers. The EN book-33 route is a placeholder slug:
 * the live slug can change, so callers resolve it at runtime (probeEnBook33Slug)
 * and either substitute the real slug or drop the route.
 */
export const AUDIT_ROUTES: readonly Route[] = [
  { name: "home-ru", path: "/" },
  { name: "home-en", path: "/en/" },
  { name: "books-ru", path: "/books/" },
  { name: "book-33-ru", path: "/books/33-ya-esm-vsadnik-kon-i-mech/" },
  { name: "book-33-en", path: "/en/books/33-i-am-the-horseman-the-horse-and-the-sword/" },
  { name: "conceptosphere-ru", path: "/conceptosphere/" },
  { name: "conceptosphere-en", path: "/en/conceptosphere/" },
  { name: "search-ru", path: "/search/" },
  { name: "downloads-ru", path: "/downloads/" },
  { name: "poetry-ru", path: "/poetry/" },
  { name: "about-ru", path: "/about/" },
  { name: "mission-ru", path: "/mission/" },
  { name: "svetozar-ru", path: "/svetozar/" },
  { name: "license-ru", path: "/license/" },
  { name: "support-ru", path: "/support/" },
  { name: "poem-1-ru", path: "/poetry/01-a-esli-budu-ya-ne-prav/" },
  { name: "project-eai-ru", path: "/projects/enlightened-ai/" },
  // No /en/ project landing on purpose: EN project content is deferred (projects
  // are RU-only today, no en.md under src/content/projects/). The gate asserts
  // status < 400, so adding a phantom EN project route would be a false failure.
  // Re-add only once the EN landing actually exists.
];

/** The name of the EN book-33 route whose slug is resolved at runtime. */
export const EN_BOOK_33_ROUTE = "book-33-en";

/** Just the route names, in order — a stable parametrization key for the gate. */
export const AUDIT_ROUTE_NAMES: readonly string[] = AUDIT_ROUTES.map((r) => r.name);

// --- pure helpers (unit-tested) ---------------------------------------------

/**
 * Read `--tag=<value>` or `--tag <value>` from argv, falling back when absent or
 * empty. Both forms are supported on purpose: the old scripts documented the
 * space form but only matched the `=` form.
 */
export function parseTag(argv: readonly string[], fallback: string): string {
  for (const [i, arg] of argv.entries()) {
    if (arg.startsWith("--tag=")) return arg.slice("--tag=".length) || fallback;
    if (arg === "--tag") return argv.at(i + 1) || fallback;
  }
  return fallback;
}

/** Stable screenshot filename, e.g. `dark-desktop-home-ru.png` or `…-mid.png`. */
export function screenshotName(
  theme: Theme,
  viewport: string,
  route: string,
  variant?: string,
): string {
  const suffix = variant ? `-${variant}` : "";
  return `${theme}-${viewport}-${route}${suffix}.png`;
}

/** Scroll offset for the "mid of page" snapshot: half the page, capped at 1200px. */
export function midScrollY(scrollHeight: number): number {
  return Math.min(Math.floor(scrollHeight * 0.5), 1200);
}

/** True when the document is wider than its viewport (a tell for line-wrap bugs). */
export function overflows(metrics: { scrollWidth: number; clientWidth: number }): boolean {
  return metrics.scrollWidth - metrics.clientWidth > 1;
}

/** Extra settle time a route needs after load. The graph canvas needs a beat to paint. */
export function settleMsFor(path: string): number {
  return path.includes("/conceptosphere") ? CONCEPTOSPHERE_SETTLE_MS : 0;
}

/** Match an `/en/books/<slug>/` href that names book 33. Shared by the live probe. */
export function isEnBook33Href(href: string): boolean {
  return /\/en\/books\/[^/]+\/?$/.test(href) && /33|horseman|sword/i.test(href);
}

// --- live EN book-33 slug probe (shared by the gate spec and the generator) --

/**
 * Find the live EN slug for book 33 by scanning /en/books/. The EN slug can
 * change, so neither the gate nor the generator may hard-code it; both resolve
 * it from the running site and either substitute it or drop the route.
 */
export async function probeEnBook33Slug(page: Page, baseUrl: string = BASE_URL): Promise<string | null> {
  try {
    await page.goto(`${baseUrl}/en/books/`, { waitUntil: "domcontentloaded" });
    return await page.evaluate(() => {
      const links = Array.from(document.querySelectorAll("a[href*='/en/books/']"))
        .map((a) => a.getAttribute("href"))
        .filter((h): h is string => h !== null);
      // Inlined twin of isEnBook33Href — page.evaluate runs in the browser and
      // cannot see Node-side imports.
      return links.find((h) => /\/en\/books\/[^/]+\/?$/.test(h) && /33|horseman|sword/i.test(h)) ?? null;
    });
  } catch {
    return null;
  }
}

/**
 * Resolve AUDIT_ROUTES against the running site: substitute the probed EN
 * book-33 slug, or drop that route if the book is absent. Returns a fresh array.
 */
export async function resolveAuditRoutes(page: Page, baseUrl: string = BASE_URL): Promise<Route[]> {
  const routes = AUDIT_ROUTES.map((r) => ({ ...r }));
  const idx = routes.findIndex((r) => r.name === EN_BOOK_33_ROUTE);
  if (idx < 0) return routes;
  const slug = await probeEnBook33Slug(page, baseUrl);
  if (slug) routes[idx] = { name: EN_BOOK_33_ROUTE, path: slug };
  else routes.splice(idx, 1);
  return routes;
}

// --- browser glue (used by the capture generators, not by the spec) ---------

/** mkdir -p, awaited. */
export async function ensureDir(path: string): Promise<void> {
  await mkdir(path, { recursive: true });
}

/** Launch Chromium, run `fn`, and always close the browser. */
export async function withBrowser<T>(fn: (browser: Browser) => Promise<T>): Promise<T> {
  const browser = await chromium.launch();
  try {
    return await fn(browser);
  } finally {
    await browser.close();
  }
}

export type ContextOptions = { deviceScaleFactor?: number };

/**
 * A browser context pinned to a theme and viewport. The theme is seeded both as
 * an OS-level `colorScheme` and as the `pncr-theme` localStorage key the site's
 * ThemeInit reads, so snapshots don't depend on `prefers-color-scheme`.
 */
export async function themedContext(
  browser: Browser,
  theme: Theme,
  viewport: Viewport,
  options: ContextOptions = {},
): Promise<BrowserContext> {
  const context = await browser.newContext({
    viewport,
    colorScheme: theme,
    reducedMotion: "reduce",
    ...(options.deviceScaleFactor ? { deviceScaleFactor: options.deviceScaleFactor } : {}),
  });
  await seedTheme(context, theme);
  return context;
}

/**
 * Seed the `pncr-theme` localStorage key so the page renders the chosen theme
 * regardless of `prefers-color-scheme`. Works on a context (generators) or a
 * page (the gate spec) — both accept addInitScript with the same shape.
 */
export async function seedTheme(
  target: { addInitScript: BrowserContext["addInitScript"] },
  theme: Theme,
): Promise<void> {
  await target.addInitScript(
    ([key, value]) => {
      try {
        localStorage.setItem(key, value);
      } catch {
        /* storage may be blocked; the OS colorScheme still applies */
      }
    },
    [THEME_STORAGE_KEY, theme] as const,
  );
}

export type VisitOptions = { timeoutMs?: number; settleMs?: number };

/**
 * Navigate and wait until the page is visually stable: network idle, webfonts
 * ready (so text metrics don't shift), and an optional per-route settle delay.
 * Throws if navigation fails; callers decide how to record that.
 */
export async function gotoStable(page: Page, url: string, options: VisitOptions = {}): Promise<void> {
  const { timeoutMs = 30_000, settleMs = 0 } = options;
  await page.goto(url, { waitUntil: "networkidle", timeout: timeoutMs });
  await waitForFontsReady(page);
  if (settleMs > 0) await page.waitForTimeout(settleMs);
}

/** Wait for webfonts to settle so text metrics (and overflow checks) are stable. */
export async function waitForFontsReady(page: Page): Promise<void> {
  try {
    await page.waitForFunction(() => document.fonts?.ready, null, { timeout: 5_000 });
  } catch {
    /* fonts.ready unsupported or slow; the page is still usable */
  }
}
