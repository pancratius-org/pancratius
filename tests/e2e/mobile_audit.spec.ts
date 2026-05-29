// Phase 6 mobile + theme-flash audit. Not part of the regular smoke suite —
// gated by AUDIT=1. Reports rather than asserts on most metrics; the only
// hard assertion is "no horizontal overflow on any audited page at 360px
// and 700px". Theme flash is detected by reading data-theme on the very
// first paint and comparing to the persisted preference.
import { test, expect } from "@playwright/test";

const AUDIT_PAGES = [
  "/",
  "/books/",
  "/books/33-ya-esm-vsadnik-kon-i-mech/",
  "/poetry/",
  "/poetry/01-a-esli-budu-ya-ne-prav/",
  "/projects/",
  "/projects/enlightened-ai/",
  "/projects/enlightened-ai/classification/",
  "/projects/enlightened-ai/manifesto/",
  "/projects/enlightened-ai/awakening/",
  "/projects/enlightened-ai/self-inquiry/",
  "/projects/enlightened-ai/concept/",
  "/projects/enlightened-ai/charter/",
  "/projects/holy-rus/",
  "/projects/holy-rus/reform-principles/",
  "/projects/holy-rus/economy-of-light/",
  "/projects/holy-rus/constitution/",
  "/projects/holy-rus/path/",
  "/projects/holy-rus/dream-of-dar/",
  "/projects/holy-rus/tartaria/",
  "/conceptosphere/",
  "/about/",
  "/mission/",
  "/svetozar/",
  "/license/",
  "/support/",
  "/downloads/",
  "/search/",
  "/en/",
  "/en/books/",
  "/en/conceptosphere/",
] as const;

const VIEWPORTS = [
  { name: "360", width: 360, height: 720 },
  { name: "700", width: 700, height: 900 },
] as const;

test.skip(!process.env.AUDIT, "set AUDIT=1 to run the mobile + theme audit probe");

for (const vp of VIEWPORTS) {
  test.describe(`viewport ${vp.name}px`, () => {
    test.use({ viewport: { width: vp.width, height: vp.height } });
    for (const path of AUDIT_PAGES) {
      test(`no overflow ${path}`, async ({ page }) => {
        await page.goto(path, { waitUntil: "domcontentloaded" });
        // Wait for fonts so wide measurements settle.
        await page.evaluate(() => document.fonts.ready);
        const overflow = await page.evaluate(() => {
          return {
            scrollW: document.documentElement.scrollWidth,
            clientW: document.documentElement.clientWidth,
            innerW: window.innerWidth,
          };
        });
        const diff = overflow.scrollW - overflow.clientW;
        // Allow 1px slop for sub-pixel rounding.
        expect(
          diff,
          `horizontal overflow on ${path} @ ${vp.name}px: scrollWidth=${overflow.scrollW} clientWidth=${overflow.clientW}`,
        ).toBeLessThanOrEqual(1);
      });
    }
  });
}

test.describe("theme-flash first-paint", () => {
  for (const cs of ["light", "dark"] as const) {
    for (const path of ["/", "/books/", "/books/33-ya-esm-vsadnik-kon-i-mech/", "/en/conceptosphere/"]) {
      test(`prefers-${cs} on ${path}`, async ({ browser }) => {
        const ctx = await browser.newContext({ colorScheme: cs });
        const p = await ctx.newPage();
        // Read data-theme as early as we can — DOMContentLoaded fires
        // after the inline ThemeInit script. If the inline script did its
        // job, data-theme is set to the expected value at that moment.
        await p.goto(path, { waitUntil: "domcontentloaded" });
        const theme = await p.evaluate(() => document.documentElement.getAttribute("data-theme"));
        expect(theme, `data-theme on first paint of ${path} with prefers-${cs}`).toBe(cs);
        await ctx.close();
      });
    }
  }
});
