#!/usr/bin/env -S node --experimental-strip-types
// Drive Lighthouse against the running preview and print a compact Markdown
// scorecard. Uses the Lighthouse CLI via `npx` so we don't wire chrome-launcher
// in by hand.
//
// SCOPE: an OPTIONAL, local-only performance diagnostic — not a verification gate
// and deliberately NOT run in CI. It fetches `lighthouse@13` over the network on
// demand via `npx` (intentionally not a locked dependency, to avoid a heavy devDep
// for a rarely-run report), so it needs network access and is non-reproducible by
// design. Making it a locked check would mean pinning lighthouse as a dependency.
//
// Usage:
//   node scripts/visual/lighthouse.ts
//
// Writes per-page JSON to .cache/visual-audit/lighthouse/<viewport>-<name>.json
// plus a summary.json, and prints the scorecard table.

import { spawn } from "node:child_process";
import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { exit, stdout } from "node:process";
import { BASE_URL, CACHE_ROOT, ensureDir, type Route } from "./harness.ts";

const OUT_DIR = `${CACHE_ROOT}/lighthouse`;

export type LhFormFactor = "mobile" | "desktop";

const ROUTES: readonly Route[] = [
  { name: "home-ru", path: "/" },
  { name: "home-en", path: "/en/" },
  { name: "books-ru", path: "/books/" },
  { name: "book-33-ru", path: "/books/33-ya-esm-vsadnik-kon-i-mech/" },
  { name: "conceptosphere-ru", path: "/conceptosphere/" },
  { name: "search-ru", path: "/search/" },
  { name: "downloads-ru", path: "/downloads/" },
];

const VIEWPORTS: readonly LhFormFactor[] = ["mobile", "desktop"];

// --- pure helpers (unit-tested) ---------------------------------------------

type LhCategory = { score?: number | null };
type LhAuditItem = { url?: string };
type LhAudit = { numericValue?: number; displayValue?: string; details?: { items?: LhAuditItem[] } };
export type LighthouseReport = {
  categories?: Record<string, LhCategory | undefined>;
  audits?: Record<string, LhAudit | undefined>;
};

export type Scores = { perf: number; a11y: number; bp: number; seo: number };
export type Diag = {
  lcp: string; cls: string; tbt: string; ttfb: string;
  renderBlocked: string[]; unusedJsKb: number; imgSizing: number;
};
export type Row = { name: string; route: string; viewport: LhFormFactor; scores: Scores; diag: Diag };

/** Lighthouse category score (0–1, possibly null) as a 0–100 integer. */
export function categoryScore(category: LhCategory | undefined): number {
  return Math.round((category?.score ?? 0) * 100);
}

/** Pull the scores and diagnostics we report out of a raw Lighthouse JSON report. */
export function summarizeReport(report: LighthouseReport): { scores: Scores; diag: Diag } {
  const cats = report.categories ?? {};
  const audits = report.audits ?? {};
  const display = (id: string): string => audits[id]?.displayValue ?? "";
  const numeric = (id: string): number | undefined => audits[id]?.numericValue;
  const items = (id: string): LhAuditItem[] => audits[id]?.details?.items ?? [];
  return {
    scores: {
      perf: categoryScore(cats.performance),
      a11y: categoryScore(cats.accessibility),
      bp: categoryScore(cats["best-practices"]),
      seo: categoryScore(cats.seo),
    },
    diag: {
      lcp: display("largest-contentful-paint"),
      cls: display("cumulative-layout-shift"),
      tbt: display("total-blocking-time"),
      ttfb: display("server-response-time"),
      renderBlocked: items("render-blocking-resources").map((i) => i.url ?? "").filter(Boolean).slice(0, 3),
      unusedJsKb: Math.round((numeric("unused-javascript") ?? 0) / 1024),
      imgSizing: items("uses-responsive-images").length,
    },
  };
}

/** Build the Lighthouse CLI args. Mobile is the default form factor; desktop needs the preset. */
export function lighthouseArgs(url: string, viewport: LhFormFactor, outFile: string): string[] {
  const args = [
    "-y", "lighthouse@13", url,
    "--quiet",
    "--output=json",
    `--output-path=${outFile}`,
    "--only-categories=performance,accessibility,best-practices,seo",
    "--chrome-flags=--headless=new --no-sandbox --disable-dev-shm-usage",
    "--throttling-method=simulate",
    "--max-wait-for-load=45000",
  ];
  if (viewport === "desktop") args.push("--preset=desktop");
  return args;
}

export function lighthouseOutName(viewport: LhFormFactor, name: string): string {
  return `${viewport}-${name}.json`;
}

export function scoresTable(rows: readonly Row[]): string {
  const header = "| page | vp | perf | a11y | bp | seo | LCP | CLS | TBT | TTFB |";
  const sep = "|---|---|---:|---:|---:|---:|---|---|---|---|";
  const body = rows.map(
    (r) =>
      `| ${r.name} | ${r.viewport} | ${r.scores.perf} | ${r.scores.a11y} | ${r.scores.bp} | ${r.scores.seo} | ${r.diag.lcp} | ${r.diag.cls} | ${r.diag.tbt} | ${r.diag.ttfb} |`,
  );
  return [header, sep, ...body].join("\n");
}

// --- process glue -----------------------------------------------------------

function run(cmd: string, args: readonly string[]): Promise<void> {
  return new Promise((resolveRun, rejectRun) => {
    const child = spawn(cmd, [...args], { stdio: ["ignore", "pipe", "pipe"] });
    let err = "";
    child.stderr?.on("data", (chunk: Buffer) => {
      err += chunk.toString();
    });
    child.on("error", rejectRun);
    child.on("close", (code) => {
      if (code === 0) resolveRun();
      else rejectRun(new Error(`${cmd} ${args.join(" ")} exit ${code}: ${err.slice(-400)}`));
    });
  });
}

async function runLighthouse(route: Route, viewport: LhFormFactor): Promise<Row> {
  const outFile = resolve(OUT_DIR, lighthouseOutName(viewport, route.name));
  await run("npx", lighthouseArgs(`${BASE_URL}${route.path}`, viewport, outFile));
  const report = JSON.parse(await readFile(outFile, "utf-8")) as LighthouseReport;
  const { scores, diag } = summarizeReport(report);
  return { name: route.name, route: route.path, viewport, scores, diag };
}

async function main(): Promise<void> {
  await ensureDir(OUT_DIR);
  const rows: Row[] = [];
  for (const route of ROUTES) {
    for (const viewport of VIEWPORTS) {
      stdout.write(`> ${viewport} ${route.name} ${route.path} …`);
      const started = Date.now();
      try {
        const row = await runLighthouse(route, viewport);
        rows.push(row);
        const { perf, a11y, bp, seo } = row.scores;
        stdout.write(` perf=${perf} a11y=${a11y} bp=${bp} seo=${seo} (${((Date.now() - started) / 1000).toFixed(1)}s)\n`);
      } catch (err) {
        stdout.write(` FAIL ${err instanceof Error ? err.message : err}\n`);
      }
    }
  }

  console.log(`\n${scoresTable(rows)}`);
  await writeFile(resolve(OUT_DIR, "summary.json"), JSON.stringify(rows, null, 2));
  console.log(`\nWrote summary to ${OUT_DIR}/summary.json`);
}

if (import.meta.main) {
  main().catch((err) => {
    console.error(err);
    exit(2);
  });
}
