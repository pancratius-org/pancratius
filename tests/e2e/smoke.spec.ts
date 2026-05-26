/*
 * Phase 6 smoke. Verifies the production build of the static site renders
 * without console errors, hits every primary surface in both languages,
 * and exercises the two interactive controls (theme toggle, language
 * switcher) end-to-end.
 *
 * The test target is `npm run preview` against the existing `dist/`. The
 * preview server is launched by `playwright.config.ts`; tests assume the
 * `webServer` block in that config has already produced a build.
 *
 * Representative book slug: `33-ya-esm-vsadnik-kon-i-mech` — RU-only, so it
 * doubles as the "EN button disabled" probe for the language switcher.
 * Paired slug (RU + EN) for the navigates-correctly probe:
 * `01-evangelie-tsarstviya`.
 */
import { expect, test, type Page, type ConsoleMessage } from "@playwright/test";

const PAIRED_BOOK_RU = "/books/01-evangelie-tsarstviya/";
const PAIRED_BOOK_EN = "/en/books/01-evangelie-tsarstviya/";
const UNPAIRED_BOOK  = "/books/33-ya-esm-vsadnik-kon-i-mech/";

/**
 * Attach a console listener that fails the test on `console.error` /
 * uncaught exceptions. We allow warnings (Pagefind emits them on the
 * search route when run against the dev server, and they're not failures).
 */
function failOnConsoleErrors(page: Page): { messages: string[] } {
  const messages: string[] = [];
  page.on("console", (msg: ConsoleMessage) => {
    if (msg.type() === "error") messages.push(`[console.error] ${msg.text()}`);
  });
  page.on("pageerror", (err) => {
    messages.push(`[pageerror] ${err.message}`);
  });
  return { messages };
}

test.describe("homepage renders in both languages", () => {
  for (const path of ["/", "/en/"]) {
    test(`GET ${path}`, async ({ page }) => {
      const { messages } = failOnConsoleErrors(page);
      const resp = await page.goto(path, { waitUntil: "domcontentloaded" });
      expect(resp?.status() ?? 0).toBeLessThan(400);
      // Site banner / sticky header is the dependable identity probe.
      await expect(page.locator("header.rail")).toBeVisible();
      expect(messages, messages.join("\n")).toEqual([]);
    });
  }
});

test.describe("books index lists books", () => {
  for (const path of ["/books/", "/en/books/"]) {
    test(`GET ${path}`, async ({ page }) => {
      const { messages } = failOnConsoleErrors(page);
      await page.goto(path, { waitUntil: "domcontentloaded" });
      // Each book card links to a `/books/<slug>/` (or `/en/books/<slug>/`).
      // Counting >= 5 is plenty to confirm the listing wired up.
      const localePrefix = path.startsWith("/en/") ? "/en/books/" : "/books/";
      const cardLinks = page.locator(`a[href^="${localePrefix}"]:not([href$="/books/"]):not([href$="/en/books/"])`);
      expect(await cardLinks.count()).toBeGreaterThanOrEqual(5);
      expect(messages, messages.join("\n")).toEqual([]);
    });
  }

  // Regression guard: an earlier LibraryFilter shape painted the "empty" line
  // because its inline IIFE ran before the cards parsed. The empty paragraph
  // is on the page (hidden by default); when books are present it must stay
  // hidden once the filter init has run.
  test("library filter does not show empty state with books present", async ({ page }) => {
    await page.goto("/books/", { waitUntil: "domcontentloaded" });
    // Wait for the script to have run. Filter init runs on DOMContentLoaded
    // or immediately if document.readyState != "loading".
    await page.locator(".book").first().waitFor();
    const emptyHidden = await page.locator("#libEmpty").evaluate(el => (el as HTMLElement).hidden);
    expect(emptyHidden).toBe(true);
    expect(await page.locator(".book").count()).toBeGreaterThanOrEqual(20);
  });
});

test.describe("conceptosphere loads graph runtime", () => {
  for (const path of ["/conceptosphere/", "/en/conceptosphere/"]) {
    test(`GET ${path}`, async ({ page }) => {
      const { messages } = failOnConsoleErrors(page);
      await page.goto(path, { waitUntil: "domcontentloaded" });
      const viewport = page.viewportSize();
      const isMobile = !!viewport && viewport.width <= 700;
      if (isMobile) {
        // Mobile fallback is a server-rendered list — must be present.
        await expect(page.locator(".cs-mobile, [data-cs-mobile-list]").first()).toBeVisible();
      } else {
        // Desktop: stage container is rendered server-side; Sigma mounts
        // canvases under it once the runtime boots. Wait for either the
        // canvases (success) or a `.cs-load-error` (which we'd want to fail
        // on). A 5 s window covers slow CI; the network is local.
        await page.waitForFunction(() => {
          const stage = document.getElementById("cs-graph");
          if (!stage) return false;
          if (stage.querySelector(".cs-load-error")) return true;
          return !!stage.querySelector("canvas");
        }, undefined, { timeout: 8000 });
        const loadErrorVisible = await page.locator("#cs-graph .cs-load-error").count();
        expect(loadErrorVisible).toBe(0);
      }
      expect(messages, messages.join("\n")).toEqual([]);
    });
  }

  // Regression guard: a prior wireMobile shape captured `filterSets` in a TDZ
  // and threw `ReferenceError: Cannot access 'filterSets' before initialization`
  // on every mobile load. Run a phone-sized viewport against /conceptosphere/
  // and assert no console errors.
  test("mobile load of /conceptosphere/ has no console errors", async ({ browser }) => {
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const p = await ctx.newPage();
    const { messages } = failOnConsoleErrors(p);
    await p.goto("/conceptosphere/", { waitUntil: "domcontentloaded" });
    await expect(p.locator(".cs-mobile").first()).toBeVisible();
    // Click into the mobile mode toggle to exercise wireMobile's event
    // listeners — that's where the TDZ ReferenceError used to fire.
    const toggle = p.locator(".cs-mode-toggle--mobile button[data-mode='books']");
    await toggle.waitFor({ state: "visible" });
    await toggle.click();
    expect(messages, messages.join("\n")).toEqual([]);
    await ctx.close();
  });
});

test.describe("representative book renders prose + colophon", () => {
  test("GET /books/33-ya-esm-vsadnik-kon-i-mech/", async ({ page }) => {
    const { messages } = failOnConsoleErrors(page);
    await page.goto(UNPAIRED_BOOK, { waitUntil: "domcontentloaded" });
    // Prose body lives in <article data-pagefind-body>; colophon is the
    // closing block with download links + license note.
    await expect(page.locator("article").first()).toBeVisible();
    await expect(page.locator(".colophon, [data-colophon], footer.colophon").first()).toBeVisible();
    expect(messages, messages.join("\n")).toEqual([]);
  });
});

test.describe("theme toggle persists", () => {
  test("clicking #themeBtn flips data-theme and writes localStorage", async ({ page }) => {
    await page.goto("/", { waitUntil: "domcontentloaded" });
    const root = page.locator("html");
    const before = await root.getAttribute("data-theme");
    expect(before === "light" || before === "dark").toBeTruthy();

    await page.locator("#themeBtn").click();
    const after = await root.getAttribute("data-theme");
    expect(after).not.toBe(before);

    const stored = await page.evaluate(() => localStorage.getItem("pncr-theme"));
    expect(stored).toBe(after);
  });
});

test.describe("language switcher", () => {
  test("paired book — clicking EN navigates to the EN sibling", async ({ page }) => {
    await page.goto(PAIRED_BOOK_RU, { waitUntil: "domcontentloaded" });
    // Switcher renders an <a> for the alternate locale. Pick the EN link via
    // hreflang to avoid coupling to wrapper class names.
    const enLink = page.locator('nav.lang a[hreflang="en"]');
    await expect(enLink).toBeVisible();
    await enLink.click();
    await page.waitForURL(`**${PAIRED_BOOK_EN}`);
    expect(new URL(page.url()).pathname).toBe(PAIRED_BOOK_EN);
  });

  test("unpaired book — EN button is disabled", async ({ page }) => {
    await page.goto(UNPAIRED_BOOK, { waitUntil: "domcontentloaded" });
    // No anchor for EN; the disabled fallback renders a <span aria-disabled>.
    const enAnchor = page.locator('nav.lang a[hreflang="en"]');
    expect(await enAnchor.count()).toBe(0);
    const disabled = page.locator('nav.lang [aria-disabled="true"]');
    await expect(disabled).toBeVisible();
  });
});
