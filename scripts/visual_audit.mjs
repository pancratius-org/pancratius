// scripts/visual_audit.mjs
// Visual audit: capture full-page screenshots at desktop and mobile widths,
// in both light and dark themes, for the surfaces the audit brief calls out.
//
// Usage:
//   node scripts/visual_audit.mjs           # default: dark + light, desktop + mobile
//   node scripts/visual_audit.mjs --tag after
//
// Writes to .cache/visual-audit/<tag>/<theme>-<vp>-<route>.png.
//
// Also exits non-zero if any page reports console errors or has
// horizontal overflow on the mobile viewport, so it doubles as a quick
// regression check.

import { chromium } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { argv } from "node:process";

const BASE = process.env.BASE_URL ?? "http://localhost:4321";

const tagArg = argv.find((a) => a.startsWith("--tag="));
const TAG = tagArg ? tagArg.split("=", 2)[1] : "before";

const ROUTES = [
  ["home-ru",            "/"],
  ["home-en",            "/en/"],
  ["books-ru",           "/books/"],
  ["book-33-ru",         "/books/33-ya-esm-vsadnik-kon-i-mech/"],
  ["book-33-en",         "/en/books/33-i-am-the-horseman-the-horse-and-the-sword/"],
  ["conceptosphere-ru",  "/conceptosphere/"],
  ["conceptosphere-en",  "/en/conceptosphere/"],
  ["search-ru",          "/search/"],
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

const VIEWPORTS = [
  ["desktop", { width: 1440, height: 900 }],
  ["mobile",  { width:  375, height: 812 }],
];

const THEMES = ["dark", "light"];

async function probeBook33EnSlug(page) {
  // Find the actual EN slug for book 33 by visiting /en/books/ and grepping.
  try {
    await page.goto(`${BASE}/en/books/`, { waitUntil: "domcontentloaded" });
    const href = await page.evaluate(() => {
      const a = Array.from(document.querySelectorAll("a[href*='/en/books/']"))
        .map((x) => x.getAttribute("href"))
        .find((h) => h && /\/en\/books\/[^/]+\/?$/.test(h) && /33|horseman|sword/i.test(h));
      return a || null;
    });
    return href;
  } catch (_) {
    return null;
  }
}

async function main() {
  const outRoot = `.cache/visual-audit/${TAG}`;
  await mkdir(outRoot, { recursive: true });

  const browser = await chromium.launch();
  const issues = [];

  // First, resolve the unknown EN book-33 slug.
  const probeCtx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const probePage = await probeCtx.newPage();
  const en33 = await probeBook33EnSlug(probePage);
  await probeCtx.close();
  if (en33) {
    const idx = ROUTES.findIndex(([name]) => name === "book-33-en");
    if (idx >= 0) ROUTES[idx] = ["book-33-en", en33];
  } else {
    // Drop the EN book route if not present.
    const idx = ROUTES.findIndex(([name]) => name === "book-33-en");
    if (idx >= 0) ROUTES.splice(idx, 1);
  }

  for (const theme of THEMES) {
    for (const [vpName, viewport] of VIEWPORTS) {
      const ctx = await browser.newContext({
        viewport,
        colorScheme: theme === "dark" ? "dark" : "light",
        reducedMotion: "reduce",
      });
      // Seed the theme that ThemeInit reads, so we don't depend on prefers-color-scheme.
      await ctx.addInitScript((t) => {
        try { localStorage.setItem("pncr-theme", t); } catch (_) {}
      }, theme);

      for (const [name, route] of ROUTES) {
        const page = await ctx.newPage();
        const errs = [];
        page.on("pageerror", (err) => errs.push(`pageerror: ${err.message}`));
        page.on("console", (msg) => {
          if (msg.type() === "error") errs.push(`console: ${msg.text()}`);
        });
        const url = `${BASE}${route}`;
        try {
          await page.goto(url, { waitUntil: "networkidle", timeout: 30000 });
        } catch (e) {
          issues.push({ route, theme, vpName, kind: "load", detail: String(e?.message ?? e) });
          await page.close();
          continue;
        }

        // Give Source Serif a moment.
        try { await page.waitForFunction(() => document.fonts && document.fonts.ready, null, { timeout: 5000 }); } catch {}

        // For conceptosphere, the canvas needs a beat for sigma to paint.
        if (route.includes("/conceptosphere")) {
          await page.waitForTimeout(1200);
        }

        const file = `${outRoot}/${theme}-${vpName}-${name}.png`;
        try {
          await page.screenshot({ path: file, fullPage: true });
        } catch (e) {
          issues.push({ route, theme, vpName, kind: "screenshot", detail: String(e?.message ?? e) });
        }

        // Check for horizontal overflow on mobile (a tell for line-wrap issues).
        if (vpName === "mobile") {
          const overflow = await page.evaluate(() => {
            const doc = document.documentElement;
            const body = document.body;
            const w = Math.max(doc.scrollWidth, body.scrollWidth);
            const cw = doc.clientWidth;
            return { w, cw, overflows: w - cw > 1 };
          });
          if (overflow.overflows) {
            issues.push({
              route, theme, vpName, kind: "h-overflow",
              detail: `scrollWidth=${overflow.w} clientWidth=${overflow.cw}`,
            });
          }
        }

        if (errs.length) {
          issues.push({ route, theme, vpName, kind: "console", detail: errs.join(" | ") });
        }
        await page.close();
      }
      await ctx.close();
    }
  }

  await browser.close();

  if (issues.length) {
    console.log("\nVisual audit issues:");
    for (const it of issues) {
      console.log(`  [${it.kind}] ${it.theme} ${it.vpName} ${it.route} → ${it.detail}`);
    }
    process.exitCode = 1;
  } else {
    console.log("Visual audit OK — no console errors, no horizontal overflow.");
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(2);
});
