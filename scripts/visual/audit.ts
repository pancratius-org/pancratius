#!/usr/bin/env -S node --experimental-strip-types
// Full-page snapshot GENERATOR: writes one full-page screenshot per
// theme × viewport × route to .cache/visual-audit/<tag>/, for human design
// review. This is NOT a pass/fail gate — the console-error and overflow checks
// that used to live here are now a real Playwright spec
// (tests/visual_audit.spec.ts). This script only captures pixels.
//
// Usage:
//   node scripts/visual/audit.ts                 # dark+light, desktop+mobile
//   node scripts/visual/audit.ts --tag after     # label the output folder
//
// Writes .cache/visual-audit/<tag>/<theme>-<viewport>-<route>.png

import { argv, exit, stdout } from "node:process";
import type { Browser } from "@playwright/test";
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
      for (const { name: viewportName, viewport } of DESKTOP_MOBILE) {
        const context = await themedContext(browser, theme, viewport);
        for (const route of routes) {
          const page = await context.newPage();
          try {
            await gotoStable(page, `${BASE_URL}${route.path}`, { settleMs: settleMsFor(route.path) });
            const file = `${outDir}/${screenshotName(theme, viewportName, route.name)}`;
            await page.screenshot({ path: file, fullPage: true });
            files.push(file);
          } catch (err) {
            stdout.write(`SKIP ${theme} ${viewportName} ${route.path}: ${err instanceof Error ? err.message : String(err)}\n`);
          }
          await page.close();
        }
        await context.close();
      }
    }
    return files;
  });

  console.log(`Wrote ${written.length} screenshots to ${outDir}`);
}

if (import.meta.main) {
  main().catch((err) => {
    console.error(err);
    exit(2);
  });
}
