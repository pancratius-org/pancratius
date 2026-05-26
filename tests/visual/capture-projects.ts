#!/usr/bin/env -S node --experimental-strip-types
// Project mini-site design-review screenshots: full-page captures of the project
// landing and the two project sections at desktop and mobile, both themes, at 2×
// for crisp review.
//
// Usage:
//   node --experimental-strip-types tests/visual/capture-projects.ts
//
// Writes .cache/visual-audit/projects/<theme>-<viewport>-<route>.png

import { exit } from "node:process";
import {
  BASE_URL,
  CACHE_ROOT,
  THEMES,
  ensureDir,
  gotoStable,
  screenshotName,
  themedContext,
  withBrowser,
  type NamedViewport,
  type Route,
} from "./harness.ts";

const OUT_DIR = `${CACHE_ROOT}/projects`;

const ROUTES: readonly Route[] = [
  { name: "index", path: "/projects/" },
  { name: "enlightened-ai", path: "/projects/enlightened-ai/" },
  { name: "holy-rus", path: "/projects/holy-rus/" },
];

// Project review uses tighter widths than the standard audit pair.
const VIEWPORTS: readonly NamedViewport[] = [
  { name: "desktop", viewport: { width: 1280, height: 900 } },
  { name: "mobile", viewport: { width: 390, height: 844 } },
];

async function main(): Promise<void> {
  await ensureDir(OUT_DIR);
  const written: string[] = [];

  await withBrowser(async (browser) => {
    for (const theme of THEMES) {
      for (const { name: viewportName, viewport } of VIEWPORTS) {
        const context = await themedContext(browser, theme, viewport, { deviceScaleFactor: 2 });
        for (const route of ROUTES) {
          const page = await context.newPage();
          await gotoStable(page, `${BASE_URL}${route.path}`);
          const file = `${OUT_DIR}/${screenshotName(theme, viewportName, route.name)}`;
          await page.screenshot({ path: file, fullPage: true });
          written.push(file);
          await page.close();
        }
        await context.close();
      }
    }
  });

  console.log(written.join("\n"));
}

if (import.meta.main) {
  main().catch((err) => {
    console.error(err);
    exit(2);
  });
}
