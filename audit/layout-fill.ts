#!/usr/bin/env node
/*
 * audit:layout-fill — surface the "lonely narrow column" class of bug.
 *
 * The recurring defect: a reading/content block is much narrower than its
 * canvas, with empty space beside it and NO sibling occupying that band — so
 * the page reads lopsided (text crammed to one side, dead air on the other).
 * This is a RENDER-geometry fact, not a static-CSS one (it depends on computed
 * widths and sibling layout), so it can only be caught at runtime. Static
 * smells (fixed-width literals) are covered by `audit:css-values`.
 *
 * For each route it loads the page, finds every main reading block (`.prose`,
 * `[data-pagefind-body]`), finds that block's CANVAS (its width owner — the
 * nearest centred/known-container ancestor), and flags the block when it fills
 * far less than the canvas AND no sibling fills the empty band. A 2-column page
 * (book TOC+body, support body+widget) passes because a sibling fills the gap;
 * a centred reading page passes because its canvas equals its measure; a
 * stranded column (the old landing, bio) is flagged.
 *
 * Usage:  node audit/layout-fill.ts            (uses BASE_URL or :4321)
 *         BASE_URL=http://localhost:4322 node audit/layout-fill.ts
 *         node audit/layout-fill.ts --gap=140  (min empty-band px to flag)
 */

import { chromium, type Page } from "@playwright/test";
import { exit } from "node:process";

const BASE_URL = process.env.BASE_URL ?? "http://localhost:4321";
const VIEWPORT = { width: 1280, height: 900 };

interface Flag {
  block: string;
  blockW: number;
  canvasW: number;
  gap: number;
  side: "left" | "right";
}

const STATIC_ROUTES = [
  "/", "/en/",
  "/books/", "/en/books/",
  "/poetry/", "/en/poetry/",
  "/videos/", "/en/videos/",
  "/messages/", "/en/messages/",
  "/mission/", "/about/", "/support/", "/downloads/", "/license/", "/search/",
  "/en/mission/", "/en/about/", "/en/support/", "/en/downloads/", "/en/license/", "/en/search/",
  "/svetozar/", "/conceptosphere/",
  "/en/svetozar/", "/en/conceptosphere/",
  "/projects/", "/projects/enlightened-ai/",
  "/projects/enlightened-ai/classification/",
  "/projects/enlightened-ai/awakening/",
  "/projects/enlightened-ai/manifesto/",
  "/projects/enlightened-ai/self-inquiry/",
];

/** In-page geometry check. Self-contained so Playwright can serialize it. */
function detect(minGap: number): Flag[] {
  const CANVAS_RE =
    /\b(l-canvas|static|col|col--|projects-index|project-page|project-subpage|home|book-page|message-page|video-page)\b/;

  const isCanvas = (n: HTMLElement): boolean => {
    if (CANVAS_RE.test(n.className)) return true;
    const cs = getComputedStyle(n);
    const ml = parseFloat(cs.marginLeft) || 0;
    const mr = parseFloat(cs.marginRight) || 0;
    return ml > 2 && Math.abs(ml - mr) < 3; // centred via auto inline margins
  };

  const canvasOf = (el: HTMLElement): { left: number; right: number; width: number } | null => {
    for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
      if (!isCanvas(p)) continue;
      const r = p.getBoundingClientRect();
      const cs = getComputedStyle(p);
      const padL = parseFloat(cs.paddingLeft) || 0;
      const padR = parseFloat(cs.paddingRight) || 0;
      return { left: r.left + padL, right: r.right - padR, width: r.width - padL - padR };
    }
    return null;
  };

  const vOverlaps = (a: DOMRect, b: DOMRect): boolean =>
    Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) >= 24;

  const inSideBand = (sr: DOMRect, r: DOMRect, c: { left: number; right: number }, side: "left" | "right"): boolean =>
    side === "right"
      ? sr.left >= r.right - 8 && sr.right <= c.right + 8
      : sr.right <= r.left + 8 && sr.left >= c.left - 8;

  const bandFilled = (el: HTMLElement, r: DOMRect, c: { left: number; right: number }, side: "left" | "right"): boolean => {
    for (const s of Array.from(document.querySelectorAll<HTMLElement>("*"))) {
      if (s === el || el.contains(s) || s.contains(el)) continue;
      const sr = s.getBoundingClientRect();
      if (sr.width < 24 || sr.height < 24 || !vOverlaps(sr, r)) continue;
      if (inSideBand(sr, r, c, side)) return true;
    }
    return false;
  };

  const flags: Flag[] = [];
  for (const el of Array.from(document.querySelectorAll<HTMLElement>(".prose, [data-pagefind-body]"))) {
    const r = el.getBoundingClientRect();
    if (r.width < 200) continue;
    const c = canvasOf(el);
    if (!c || c.width <= r.width + 80) continue; // already fills its canvas
    const side: "left" | "right" = c.right - r.right >= r.left - c.left ? "right" : "left";
    const gap = Math.max(c.right - r.right, r.left - c.left);
    if (gap < minGap || bandFilled(el, r, c, side)) continue;
    flags.push({ block: el.className || el.tagName.toLowerCase(), blockW: Math.round(r.width), canvasW: Math.round(c.width), gap: Math.round(gap), side });
  }
  return flags;
}

function routeUrl(route: string): string {
  return new URL(route, BASE_URL).toString();
}

async function probeChild(page: Page, index: string, pattern: string): Promise<string | null> {
  try {
    await page.goto(routeUrl(index), { waitUntil: "domcontentloaded", timeout: 15000 });
    return await page.evaluate((src: string) => {
      const rx = new RegExp(src);
      for (const a of Array.from(document.querySelectorAll<HTMLAnchorElement>("a[href]"))) {
        const h = a.getAttribute("href") ?? "";
        if (rx.test(h)) return h;
      }
      return null;
    }, pattern);
  } catch {
    return null;
  }
}

async function discoverRoutes(page: Page): Promise<string[]> {
  const routes = [...STATIC_ROUTES];
  const probes: [string, string][] = [
    ["/books/", "^/books/[a-z0-9-]+/$"],
    ["/en/books/", "^/en/books/[a-z0-9-]+/$"],
    ["/poetry/", "^/poetry/[a-z0-9-]+/$"],
    ["/en/poetry/", "^/en/poetry/[a-z0-9-]+/$"],
    ["/messages/", "^/messages/[a-z0-9-]+/$"],
    ["/en/messages/", "^/en/messages/[a-z0-9-]+/$"],
    ["/videos/", "^/videos/[a-z0-9-]+/$"],
    ["/en/videos/", "^/en/videos/[a-z0-9-]+/$"],
  ];
  for (const [index, pattern] of probes) {
    const href = await probeChild(page, index, pattern);
    if (href && !routes.includes(href)) routes.push(href);
  }
  return routes;
}

async function auditRoute(page: Page, route: string, minGap: number): Promise<Flag[] | null> {
  try {
    await page.goto(routeUrl(route), { waitUntil: "networkidle", timeout: 20000 });
    return await page.evaluate(detect, minGap);
  } catch {
    return null;
  }
}

function renderRoute(route: string, flags: Flag[] | null): string[] {
  if (flags === null) return [`  ${route}  (load failed)`];
  if (flags.length === 0) return [];
  const lines = [`  ${route}`];
  for (const f of flags) {
    lines.push(
      `    warning: .${f.block.split(" ")[0]} fills ${f.blockW}px of ${f.canvasW}px ` +
        `-- ${f.gap}px empty on the ${f.side}, no sibling fills it`,
    );
  }
  return lines;
}

interface AuditSummary {
  failed: number;
  flagged: number;
  lines: string[];
  routeCount: number;
}

function parseMinGap(): number {
  const gapArg = process.argv.find((a) => a.startsWith("--gap=")) ?? "--gap=130";
  const minGap = Number(gapArg.slice("--gap=".length));
  if (!Number.isFinite(minGap) || minGap < 0) {
    throw new Error(`Invalid --gap value: ${gapArg.slice("--gap=".length)}`);
  }
  return minGap;
}

async function runAudit(page: Page, minGap: number): Promise<AuditSummary> {
  const routes = await discoverRoutes(page);
  const lines: string[] = [];
  let flagged = 0;
  let failed = 0;

  for (const route of routes) {
    const flags = await auditRoute(page, route, minGap);
    if (flags === null) failed += 1;
    if (flags && flags.length > 0) flagged += flags.length;
    lines.push(...renderRoute(route, flags));
  }

  return { failed, flagged, lines, routeCount: routes.length };
}

function report(summary: AuditSummary, minGap: number): string {
  const ok = summary.flagged === 0 && summary.failed === 0;
  return [
    "Layout-fill audit (lonely narrow column / under-filled content)",
    `Swept ${summary.routeCount} routes at ${BASE_URL}, gap threshold ${minGap}px.`,
    "",
    ...(ok ? ["  ok: no under-filled content blocks found"] : summary.lines),
    "",
  ].join("\n");
}

async function main(): Promise<void> {
  const minGap = parseMinGap();
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: VIEWPORT });
    const summary = await runAudit(page, minGap);
    process.stdout.write(report(summary, minGap));
    exit(summary.flagged === 0 && summary.failed === 0 ? 0 : 1);
  } finally {
    await browser.close();
  }
}

main().catch((err) => {
  process.stderr.write(`${err instanceof Error ? err.message : String(err)}\n`);
  exit(2);
});
