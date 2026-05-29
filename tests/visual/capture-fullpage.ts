#!/usr/bin/env -S node --experimental-strip-types
// Full-page snapshot GENERATOR: writes one full-page screenshot per
// theme × viewport × route to .cache/visual-audit/<tag>/, for human design
// review. This is NOT a pass/fail gate — the console-error and overflow checks
// that used to live here are now a real Playwright spec
// (tests/visual_audit.spec.ts). This script only captures pixels.
//
// Usage:
//   node --experimental-strip-types tests/visual/capture-fullpage.ts                 # dark+light, desktop+mobile
//   node --experimental-strip-types tests/visual/capture-fullpage.ts --tag after     # label the output folder
//
// Writes .cache/visual-audit/<tag>/<theme>-<viewport>-<route>.png

import { argv, exit, stdout } from "node:process";
import type { Browser, BrowserContext } from "@playwright/test";
import {
  CACHE_ROOT,
  DESKTOP_MOBILE,
  THEMES,
  BASE_URL,
  ensureDir,
  gotoStable,
  parseTag,
  resolveAuditRoutes,
  screenshotName,
  settleMsFor,
  themedContext,
  withBrowser,
  type NamedViewport,
  type Route,
  type Theme,
} from "./harness.ts";

const TAG = parseTag(argv, "before");

/** Resolve the audit routes (live EN book-33 slug) using a throwaway context. */
async function routesFor(browser: Browser) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  try {
    return await resolveAuditRoutes(await context.newPage());
  } finally {
    await context.close();
  }
}

async function main(): Promise<void> {
  const outDir = `${CACHE_ROOT}/${TAG}`;
  await ensureDir(outDir);

  const written = await withBrowser(async (browser) => {
    const routes = await routesFor(browser);
    const files: string[] = [];
    for (const theme of THEMES) {
      for (const viewport of DESKTOP_MOBILE) {
        files.push(...await captureViewportSet(browser, outDir, theme, viewport, routes));
      }
    }
    return files;
  });

  console.log(`Wrote ${written.length} screenshots to ${outDir}`);
}

async function captureViewportSet(
  browser: Browser,
  outDir: string,
  theme: Theme,
  { name: viewportName, viewport }: NamedViewport,
  routes: readonly Route[],
): Promise<string[]> {
  const context = await themedContext(browser, theme, viewport);
  try {
    const files: string[] = [];
    for (const route of routes) {
      const file = await captureRoute(context, outDir, theme, viewportName, route);
      if (file) files.push(file);
    }
    return files;
  } finally {
    await context.close();
  }
}

async function captureRoute(
  context: BrowserContext,
  outDir: string,
  theme: Theme,
  viewportName: string,
  route: Route,
): Promise<string | null> {
  const page = await context.newPage();
  try {
    await gotoStable(page, `${BASE_URL}${route.path}`, { settleMs: settleMsFor(route.path) });
    const file = `${outDir}/${screenshotName(theme, viewportName, route.name)}`;
    await page.screenshot({ path: file, fullPage: true });
    return file;
  } catch (err) {
    stdout.write(`SKIP ${theme} ${viewportName} ${route.path}: ${err instanceof Error ? err.message : String(err)}\n`);
    return null;
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
