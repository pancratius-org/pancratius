/*
 * Analytics markup contract (docs/analytics.md) against the built artefact:
 * the tracker tag ships on every page gated to production hostnames, and the
 * observer-layer attributes are present where the vocabulary promises them.
 * Runtime tracking itself is not asserted — `data-domains` keeps the tracker
 * silent on localhost by design.
 */
import { expect, test } from "@playwright/test";

const PAIRED_BOOK = "/ru/books/01-evangelie-tsarstviya/";

test("the tracker ships with production-only domains and Web Vitals", async ({ page }) => {
  await page.goto(PAIRED_BOOK, { waitUntil: "domcontentloaded" });
  const script = page.locator('script[src="https://cloud.umami.is/script.js"]');
  await expect(script).toHaveAttribute("data-website-id", /^[0-9a-f-]{36}$/);
  await expect(script).toHaveAttribute("data-domains", "pancratius.ru,pancratius.org");
  await expect(script).toHaveAttribute("data-exclude-hash", "true");
  await expect(script).toHaveAttribute("data-performance", "true");
});

test("a book page exposes download, read, and language-switch markup", async ({ page }) => {
  await page.goto(PAIRED_BOOK, { waitUntil: "domcontentloaded" });

  const downloads = page.locator('a[data-track-event="work-download"]');
  expect(await downloads.count()).toBeGreaterThan(0);
  await expect(downloads.first()).toHaveAttribute("data-track-kind", "book");
  await expect(downloads.first()).toHaveAttribute("data-track-slug", "01-evangelie-tsarstviya");
  await expect(downloads.first()).toHaveAttribute("data-track-locale", "ru");
  await expect(downloads.first()).toHaveAttribute("data-track-format", /.+/);

  const readMarker = page.locator("[data-track-read]");
  await expect(readMarker).toHaveAttribute("data-track-kind", "book");
  await expect(readMarker).toHaveAttribute("data-track-slug", "01-evangelie-tsarstviya");

  const switchToEn = page.locator('a[data-track-event="language-switch"][data-track-to="en"]');
  await expect(switchToEn).toHaveAttribute("data-track-from", "ru");
});

test("the 404 page carries the not-found marker", async ({ page }) => {
  const resp = await page.goto("/ru/definitely-not-a-page/", { waitUntil: "domcontentloaded" });
  expect(resp?.status()).toBe(404);
  await expect(page.locator("[data-track-not-found]")).toBeAttached();
});

test("the tracker precedes the observer module in the deferred queue", async ({ page }) => {
  // Classic `defer` scripts and module scripts share the browser's in-order
  // queue, so this document order guarantees `window.umami` exists when the
  // observer binds (load-time events like `not-found` depend on it). Astro
  // emits the observer inline or as a file depending on bundle size — accept
  // both forms.
  await page.goto("/ru/", { waitUntil: "domcontentloaded" });
  const order = await page.evaluate(() => {
    const scripts = Array.from(document.head.querySelectorAll("script"));
    return {
      tracker: scripts.findIndex(s => (s.getAttribute("src") ?? "").startsWith("https://cloud.umami.is/")),
      observer: scripts.findIndex(
        s =>
          s.type === "module" &&
          ((s.getAttribute("src") ?? "").includes("UmamiAnalytics") || s.text.includes("work-download")),
      ),
    };
  });
  expect(order.tracker).toBeGreaterThanOrEqual(0);
  expect(order.observer).toBeGreaterThan(order.tracker);
});
