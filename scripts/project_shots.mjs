// Project mini-site design-review screenshots.
// Captures /projects/, /projects/enlightened-ai/, /projects/holy-rus/ at
// desktop (1280) and mobile (390), in both light and dark themes, into
// .cache/visual-audit/projects/.
import { chromium } from "@playwright/test";
import { mkdir } from "node:fs/promises";

const BASE = process.env.BASE_URL ?? "http://127.0.0.1:4321";
const OUT = ".cache/visual-audit/projects";

const ROUTES = [
  ["index", "/projects/"],
  ["enlightened-ai", "/projects/enlightened-ai/"],
  ["holy-rus", "/projects/holy-rus/"],
];
const VIEWPORTS = [
  ["desktop", { width: 1280, height: 900 }],
  ["mobile", { width: 390, height: 844 }],
];
const THEMES = ["dark", "light"];

await mkdir(OUT, { recursive: true });
const browser = await chromium.launch();
const written = [];

for (const theme of THEMES) {
  for (const [vp, viewport] of VIEWPORTS) {
    const ctx = await browser.newContext({
      viewport,
      colorScheme: theme,
      reducedMotion: "reduce",
      deviceScaleFactor: 2,
    });
    await ctx.addInitScript(t => {
      try { localStorage.setItem("pncr-theme", t); } catch {}
    }, theme);
    for (const [name, route] of ROUTES) {
      const page = await ctx.newPage();
      await page.goto(`${BASE}${route}`, { waitUntil: "networkidle", timeout: 30000 });
      try { await page.waitForFunction(() => document.fonts?.ready, null, { timeout: 5000 }); } catch {}
      const file = `${OUT}/${theme}-${vp}-${name}.png`;
      await page.screenshot({ path: file, fullPage: true });
      written.push(file);
      await page.close();
    }
    await ctx.close();
  }
}

await browser.close();
console.log(written.join("\n"));
