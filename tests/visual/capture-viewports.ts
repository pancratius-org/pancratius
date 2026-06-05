#!/usr/bin/env node
// Viewport-sized snapshots (above-the-fold) at the top and middle of each page,
// sized so each image stays under the reader-tool 2000×2000 limit. Use this when
// you want to eyeball results rather than gate a regression.
//
// Usage:
//   node tests/visual/capture-viewports.ts
//   node tests/visual/capture-viewports.ts --tag after-vp
//
// Writes .cache/visual-audit/<tag>/<theme>-<viewport>-<route>-{top,mid}.png

import { argv, exit } from "node:process";
import type { Browser, BrowserContext } from "@playwright/test";
import {
  BASE_URL,
  CACHE_ROOT,
  DESKTOP_MOBILE,
  THEMES,
  ensureDir,
  gotoStable,
  midScrollY,
  parseTag,
  screenshotName,
  settleMsFor,
  themedContext,
  withBrowser,
  type NamedViewport,
  type Route,
  type Theme,
} from "./harness.ts";

const TAG = parseTag(argv, "before-vp");

const ROUTES: readonly Route[] = [
  { name: "home-ru", path: "/" },
  { name: "home-en", path: "/en/" },
  { name: "books-ru", path: "/books/" },
  { name: "books-en", path: "/en/books/" },
  { name: "book-33-ru", path: "/books/33-ya-esm-vsadnik-kon-i-mech/" },
  { name: "book-01-en", path: "/en/books/01-evangelie-tsarstviya/" },
  { name: "conceptosphere-ru", path: "/conceptosphere/" },
  { name: "conceptosphere-en", path: "/en/conceptosphere/" },
  { name: "search-ru", path: "/search/" },
  { name: "search-en", path: "/en/search/" },
  { name: "downloads-ru", path: "/downloads/" },
  { name: "poetry-ru", path: "/poetry/" },
  { name: "about-ru", path: "/about/" },
  { name: "mission-ru", path: "/mission/" },
  { name: "svetozar-ru", path: "/svetozar/" },
  { name: "license-ru", path: "/license/" },
  { name: "support-ru", path: "/support/" },
  { name: "poem-1-ru", path: "/poetry/01-a-esli-budu-ya-ne-prav/" },
  { name: "project-eai-ru", path: "/projects/enlightened-ai/" },
  // No EN project landing — projects are RU-only today (see harness.ts).
];

async function main(): Promise<void> {
  const outDir = `${CACHE_ROOT}/${TAG}`;
  await ensureDir(outDir);

  await withBrowser(async (browser) => {
    for (const theme of THEMES) {
      for (const viewport of DESKTOP_MOBILE) {
        await captureViewportSet(browser, outDir, theme, viewport);
      }
    }
  });
}

async function captureViewportSet(
  browser: Browser,
  outDir: string,
  theme: Theme,
  { name: viewportName, viewport }: NamedViewport,
): Promise<void> {
  const context = await themedContext(browser, theme, viewport);
  try {
    for (const route of ROUTES) {
      await captureRouteSnapshots(context, outDir, theme, viewportName, route);
    }
  } finally {
    await context.close();
  }
}

async function captureRouteSnapshots(
  context: BrowserContext,
  outDir: string,
  theme: Theme,
  viewportName: string,
  route: Route,
): Promise<void> {
  const page = await context.newPage();
  try {
    await gotoStable(page, `${BASE_URL}${route.path}`, { settleMs: settleMsFor(route.path) });
    await page.screenshot({ path: `${outDir}/${screenshotName(theme, viewportName, route.name, "top")}` });

    const scrollY = midScrollY(
      await page.evaluate(() =>
        Math.max(document.body.scrollHeight, document.documentElement.scrollHeight),
      ),
    );
    if (scrollY <= 100) return;
    await page.evaluate((y) => window.scrollTo(0, y), scrollY);
    await page.waitForTimeout(200);
    await page.screenshot({ path: `${outDir}/${screenshotName(theme, viewportName, route.name, "mid")}` });
  } catch (err) {
    console.log("SKIP", route.path, theme, viewportName, err instanceof Error ? err.message : err);
  } finally {
    await page.close();
  }
}

if (import.meta.main) {
  main().catch((err) => {
    console.error(err);
    exit(2);
  });
}
