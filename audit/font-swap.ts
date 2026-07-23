#!/usr/bin/env node

// Compare the host's actual fallback geometry with Source Serif after it loads.
import { chromium, type Browser, type CDPSession, type Page, type Route } from "@playwright/test";

const BASE_URL = process.env.BASE_URL ?? "http://127.0.0.1:4321";
const MAX_HEIGHT_DELTA = 0.06;
const MAX_WIDTH_DELTA = 0.14;

type Geometry = { width: number; height: number };
type AuditCase = {
  name: string;
  path: string;
  viewport: { width: number; height: number };
};

const DESKTOP = { width: 1440, height: 900 };
const MOBILE = { width: 375, height: 812 };
const CASES: AuditCase[] = [
  { name: "home-ru desktop", path: "/ru/", viewport: DESKTOP },
  { name: "home-ru mobile", path: "/ru/", viewport: MOBILE },
  { name: "home-en mobile", path: "/en/", viewport: MOBILE },
  { name: "poem-1-ru mobile", path: "/ru/poetry/01-a-esli-budu-ya-ne-prav/", viewport: MOBILE },
  { name: "book-33-ru mobile", path: "/ru/books/33-ya-esm-vsadnik-kon-i-mech/", viewport: MOBILE },
];

async function headingGeometry(page: Page): Promise<Geometry> {
  const geometry = await page.evaluate(() => {
    const heading = document.querySelector("main h1, h1");
    if (!heading) return null;
    const box = heading.getBoundingClientRect();
    return { width: box.width, height: box.height };
  });
  if (!geometry) throw new Error("No <h1> found");
  return geometry;
}

async function headingPlatformFont(client: CDPSession): Promise<string> {
  const { root } = await client.send("DOM.getDocument", { depth: -1 });
  const { nodeId } = await client.send("DOM.querySelector", {
    nodeId: root.nodeId,
    selector: "main h1, h1",
  });
  const { fonts } = await client.send("CSS.getPlatformFontsForNode", { nodeId });
  return fonts.map((font) => font.familyName).join(", ");
}

type Swap = {
  fallbackFont: string;
  fallback: Geometry;
  webfontFont: string;
  webfont: Geometry;
};

async function measureSwap(page: Page, client: CDPSession, path: string): Promise<Swap> {
  const heldFonts: Route[] = [];
  let holdFonts = true;
  await page.route("**/*.woff2", async (route) => {
    if (holdFonts) {
      heldFonts.push(route);
      return;
    }
    await route.continue();
  });

  await page.goto(new URL(path, BASE_URL).toString(), { waitUntil: "domcontentloaded" });
  await page.waitForFunction(
    () => getComputedStyle(document.body).marginTop === "0px" && document.styleSheets.length > 0,
    null,
    { timeout: 10_000 },
  );

  const fallbackFont = await headingPlatformFont(client);
  const fallback = await headingGeometry(page);
  holdFonts = false;
  await Promise.all(heldFonts.map((route) => route.continue()));
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
  await page.waitForTimeout(200);

  return {
    fallbackFont,
    fallback,
    webfontFont: await headingPlatformFont(client),
    webfont: await headingGeometry(page),
  };
}

function delta(from: number, to: number): number {
  return Math.abs(from - to) / to;
}

async function runCase(browser: Browser, auditCase: AuditCase): Promise<boolean> {
  const page = await browser.newPage({ viewport: auditCase.viewport });
  try {
    const client = await page.context().newCDPSession(page);
    await client.send("DOM.enable");
    await client.send("CSS.enable");
    const swap = await measureSwap(page, client, auditCase.path);
    const widthDelta = delta(swap.fallback.width, swap.webfont.width);
    const heightDelta = delta(swap.fallback.height, swap.webfont.height);
    const failures = [
      ...(swap.fallbackFont.includes("Source Serif") ? ["webfont rendered before release"] : []),
      ...(!swap.webfontFont.includes("Source Serif") ? ["webfont did not render after release"] : []),
      ...(heightDelta > MAX_HEIGHT_DELTA ? ["vertical reflow"] : []),
      ...(widthDelta > MAX_WIDTH_DELTA ? ["width reflow"] : []),
    ];
    const status = failures.length === 0 ? "ok" : `FAIL: ${failures.join(", ")}`;
    process.stdout.write(
      `  ${status}  ${auditCase.name}: ${swap.fallbackFont} → ${swap.webfontFont} ` +
        `(Δw ${(widthDelta * 100).toFixed(1)}%, Δh ${(heightDelta * 100).toFixed(1)}%)\n`,
    );
    return failures.length === 0;
  } finally {
    await page.close();
  }
}

async function main(): Promise<void> {
  process.stdout.write(`Font-swap audit at ${BASE_URL}\n`);
  const browser = await chromium.launch();
  try {
    let failed = 0;
    for (const auditCase of CASES) {
      if (!(await runCase(browser, auditCase))) failed += 1;
    }
    process.exitCode = failed === 0 ? 0 : 1;
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
  process.exitCode = 2;
});
