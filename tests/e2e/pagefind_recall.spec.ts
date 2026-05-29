// Pagefind RU recall probe — Phase 6 verification gate, runs against the
// preview server. This is *not* a smoke test we ship long-term; it's the
// instrument the brief requires to measure recall for five Russian queries.
//
// Skipped by default in CI so the smoke suite stays focused; enable with
// `PAGEFIND_RECALL=1 npx playwright test pagefind_recall.spec.ts`.
import { expect, test } from "@playwright/test";

const RECALL_TARGET = 5;
const QUERIES = ["свет", "творец", "светозар", "молитва", "царствие"] as const;
const PAGEFIND_MODULE = "/pagefind/pagefind.js";

interface PagefindApi {
  init?: () => Promise<void> | void;
  search: (query: string) => Promise<{ results: unknown[] }>;
}

test.describe.configure({ mode: "serial" });
test.skip(!process.env.PAGEFIND_RECALL, "set PAGEFIND_RECALL=1 to run the recall probe");

test("RU corpus recall", async ({ page }) => {
  await page.goto("/search/", { waitUntil: "domcontentloaded" });
  await page.locator("#pfsInput").waitFor();

  // Pagefind exposes the search API as the imported module; the inline
  // script imports it dynamically. We do the same so we hit the same
  // bundled index the production page uses.
  const counts: Record<string, number> = {};
  for (const q of QUERIES) {
    const n = await page.evaluate(async (query: string) => {
      const pf = await import(PAGEFIND_MODULE) as PagefindApi;
      if (pf.init) await pf.init();
      const res = await pf.search(query);
      return res.results.length;
    }, q);
    counts[q] = n;
    console.log(`pagefind  ${q.padEnd(10)} -> ${n} hits`);
  }
  for (const [q, n] of Object.entries(counts)) {
    expect(n, `expected ≥${RECALL_TARGET} hits for '${q}', got ${n}`).toBeGreaterThanOrEqual(RECALL_TARGET);
  }
});
