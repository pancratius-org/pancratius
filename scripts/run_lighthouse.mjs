// scripts/run_lighthouse.mjs — drive lighthouse against the running preview.
//
// Usage: node scripts/run_lighthouse.mjs
//
// Writes per-page JSON to .cache/visual-audit/lighthouse/<viewport>-<name>.json
// and prints a compact table at the end. Uses headless Chromium via the lighthouse
// CLI so we don't have to wire in chrome-launcher manually.

import { spawn } from "node:child_process";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const BASE = process.env.BASE_URL ?? "http://localhost:4321";

const ROUTES = [
  ["home-ru",            "/"],
  ["home-en",            "/en/"],
  ["books-ru",           "/books/"],
  ["book-33-ru",         "/books/33-ya-esm-vsadnik-kon-i-mech/"],
  ["conceptosphere-ru",  "/conceptosphere/"],
  ["search-ru",          "/search/"],
  ["downloads-ru",       "/downloads/"],
];

const VIEWPORTS = [
  ["mobile",  "mobile"],
  ["desktop", "desktop"],
];

const OUT = ".cache/visual-audit/lighthouse";

function run(cmd, args) {
  return new Promise((res, rej) => {
    const p = spawn(cmd, args, { stdio: ["ignore", "pipe", "pipe"] });
    let out = ""; let err = "";
    p.stdout.on("data", (d) => out += d.toString());
    p.stderr.on("data", (d) => err += d.toString());
    p.on("close", (code) => code === 0 ? res({ out, err }) : rej(new Error(`${cmd} ${args.join(' ')} exit ${code}: ${err.slice(-400)}`)));
  });
}

async function audit(name, route, vp) {
  const url = `${BASE}${route}`;
  const outFile = resolve(OUT, `${vp}-${name}.json`);
  const args = [
    "-y", "lighthouse@13", url,
    "--quiet",
    "--output=json",
    `--output-path=${outFile}`,
    `--preset=${vp === "desktop" ? "desktop" : "perf"}`,
    "--only-categories=performance,accessibility,best-practices,seo",
    "--chrome-flags=--headless=new --no-sandbox --disable-dev-shm-usage",
    "--throttling-method=simulate",
    "--max-wait-for-load=45000",
  ];
  // The `--preset=perf` is mobile by default; switch to desktop-formfactor when desktop.
  if (vp === "mobile") args.splice(args.indexOf(`--preset=${vp === "desktop" ? "desktop" : "perf"}`), 1);
  await run("npx", args);
  const raw = JSON.parse(await readFile(outFile, "utf-8"));
  const cats = raw.categories ?? {};
  const score = (key) => Math.round(((cats[key]?.score ?? 0) * 100));
  const audits = raw.audits ?? {};
  const num = (id) => audits[id]?.numericValue;
  const dispVal = (id) => audits[id]?.displayValue ?? "";
  return {
    name, route, vp,
    scores: {
      perf: score("performance"),
      a11y: score("accessibility"),
      bp:   score("best-practices"),
      seo:  score("seo"),
    },
    diag: {
      lcp:           dispVal("largest-contentful-paint"),
      cls:           dispVal("cumulative-layout-shift"),
      tbt:           dispVal("total-blocking-time"),
      ttfb:          dispVal("server-response-time"),
      renderBlocked: (audits["render-blocking-resources"]?.details?.items ?? []).map((i) => i.url).slice(0, 3),
      unusedJsKb:    Math.round((num("unused-javascript") ?? 0) / 1024),
      imgSizing:     (audits["uses-responsive-images"]?.details?.items ?? []).length,
    },
  };
}

async function main() {
  await mkdir(OUT, { recursive: true });
  const rows = [];
  for (const [name, route] of ROUTES) {
    for (const [vp] of VIEWPORTS) {
      process.stdout.write(`> ${vp} ${name} ${route} …`);
      const t0 = Date.now();
      try {
        const r = await audit(name, route, vp);
        rows.push(r);
        const { perf, a11y, bp, seo } = r.scores;
        process.stdout.write(` perf=${perf} a11y=${a11y} bp=${bp} seo=${seo} (${((Date.now()-t0)/1000).toFixed(1)}s)\n`);
      } catch (e) {
        process.stdout.write(` FAIL ${e.message}\n`);
      }
    }
  }

  // Print a Markdown table for the report.
  console.log("\n| page | vp | perf | a11y | bp | seo | LCP | CLS | TBT | TTFB |");
  console.log("|---|---|---:|---:|---:|---:|---|---|---|---|");
  for (const r of rows) {
    console.log(`| ${r.name} | ${r.vp} | ${r.scores.perf} | ${r.scores.a11y} | ${r.scores.bp} | ${r.scores.seo} | ${r.diag.lcp} | ${r.diag.cls} | ${r.diag.tbt} | ${r.diag.ttfb} |`);
  }
  await writeFile(resolve(OUT, "summary.json"), JSON.stringify(rows, null, 2));
  console.log(`\nWrote summary to ${OUT}/summary.json`);
}

main().catch((e) => { console.error(e); process.exit(2); });
