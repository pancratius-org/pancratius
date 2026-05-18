// scripts/visual_audit_viewport.mjs — viewport-sized snapshots (above-the-fold)
// at multiple scroll positions, sized so each image stays under the
// reader-tool 2000x2000 limit. Use this when you want to eyeball results.

import { chromium } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { argv } from "node:process";

const BASE = process.env.BASE_URL ?? "http://localhost:4321";

const tagArg = argv.find((a) => a.startsWith("--tag="));
const TAG = tagArg ? tagArg.split("=", 2)[1] : "before-vp";

const ROUTES = [
  ["home-ru",            "/"],
  ["home-en",            "/en/"],
  ["books-ru",           "/books/"],
  ["books-en",           "/en/books/"],
  ["book-33-ru",         "/books/33-ya-esm-vsadnik-kon-i-mech/"],
  ["book-01-en",         "/en/books/01-evangelie-tsarstviya/"],
  ["conceptosphere-ru",  "/conceptosphere/"],
  ["conceptosphere-en",  "/en/conceptosphere/"],
  ["search-ru",          "/search/"],
  ["search-en",          "/en/search/"],
  ["downloads-ru",       "/downloads/"],
  ["poetry-ru",          "/poetry/"],
  ["about-ru",           "/about/"],
  ["mission-ru",         "/mission/"],
  ["svetozar-ru",        "/svetozar/"],
  ["license-ru",         "/license/"],
  ["support-ru",         "/support/"],
  ["poem-1-ru",          "/poetry/01-a-esli-budu-ya-ne-prav/"],
  ["project-eai-ru",     "/projects/enlightened-ai/"],
  ["project-eai-en",     "/en/projects/enlightened-ai/"],
];

// Desktop viewport, keep height under 2000 so the image stays viewable.
const VIEWPORTS = [
  ["desktop", { width: 1440, height: 900 }],
  ["mobile",  { width:  375, height: 812 }],
];

async function main() {
  const outRoot = `.cache/visual-audit/${TAG}`;
  await mkdir(outRoot, { recursive: true });

  const browser = await chromium.launch();

  for (const theme of ["dark", "light"]) {
    for (const [vpName, viewport] of VIEWPORTS) {
      const ctx = await browser.newContext({
        viewport,
        colorScheme: theme === "dark" ? "dark" : "light",
        reducedMotion: "reduce",
      });
      await ctx.addInitScript((t) => {
        try { localStorage.setItem("pncr-theme", t); } catch {}
      }, theme);

      for (const [name, route] of ROUTES) {
        const page = await ctx.newPage();
        const url = `${BASE}${route}`;
        try {
          await page.goto(url, { waitUntil: "networkidle", timeout: 30000 });
        } catch (e) {
          console.log("SKIP", route, theme, vpName, e.message);
          await page.close();
          continue;
        }
        try { await page.waitForFunction(() => document.fonts && document.fonts.ready, null, { timeout: 5000 }); } catch {}
        if (route.includes("/conceptosphere")) {
          await page.waitForTimeout(1200);
        }

        // Top of page.
        await page.screenshot({ path: `${outRoot}/${theme}-${vpName}-${name}-top.png` });

        // Mid of page (scroll the lesser of half-page-height vs ~1200px).
        const scrollY = await page.evaluate(() => {
          const h = Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);
          return Math.min(Math.floor(h * 0.5), 1200);
        });
        if (scrollY > 100) {
          await page.evaluate((y) => window.scrollTo(0, y), scrollY);
          await page.waitForTimeout(200);
          await page.screenshot({ path: `${outRoot}/${theme}-${vpName}-${name}-mid.png` });
        }

        await page.close();
      }
      await ctx.close();
    }
  }
  await browser.close();
}

main().catch((e) => { console.error(e); process.exit(2); });
