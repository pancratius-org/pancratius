// Visual regression GATE. The console-error + mobile-horizontal-overflow checks
// that the old `visual_audit.mjs` accumulated into an array and turned into a
// process exit code are, in truth, a test — so they live here as one, with
// real expect() assertions and good failure messages.
//
// Matrix: theme (dark, light) × viewport (desktop 1440, mobile 375) × the audit
// route set. Theme is seeded as the `pncr-theme` localStorage key the site's
// ThemeInit reads (and as the OS colorScheme), so results don't depend on
// prefers-color-scheme. Console errors are asserted on every cell; horizontal
// overflow is asserted on mobile only. The EN book-33 slug is resolved live.
//
// This is a heavy, full-matrix probe — gated off the default smoke run, like
// `mobile_audit.spec.ts` (AUDIT=1) and `pagefind_recall.spec.ts`:
//
//   VISUAL_AUDIT=1 npx playwright test visual_audit.spec.ts
//
// PNG capture for human review is a *separate* concern — see
// tests/visual/capture-fullpage.ts. This spec asserts; it does not write artifacts.
import { expect, test, type ConsoleMessage, type Page } from "@playwright/test";
import {
  AUDIT_ROUTE_NAMES,
  DESKTOP_MOBILE,
  THEMES,
  THEME_STORAGE_KEY,
  overflows,
  resolveAuditRoutes,
  settleMsFor,
  waitForFontsReady,
  type Route,
} from "../visual/harness.ts";

test.skip(!process.env.VISUAL_AUDIT, "set VISUAL_AUDIT=1 to run the full theme × viewport visual gate");

/** Routes resolved once against the running site (live EN book-33 slug). */
let auditRoutes: Route[] = [];

test.beforeAll(async ({ browser, baseURL }) => {
  // browser.newPage() creates a page with no context use-options, so the
  // config baseURL does NOT apply to its goto — resolveAuditRoutes builds an
  // absolute URL, so we pass the resolved baseURL fixture through explicitly.
  const page = await browser.newPage();
  auditRoutes = await resolveAuditRoutes(page, baseURL);
  await page.close();
});

/** Collect console errors and uncaught exceptions for an expect() at the end. */
function collectErrors(page: Page): { messages: string[] } {
  const messages: string[] = [];
  page.on("pageerror", (err) => messages.push(`[pageerror] ${err.message}`));
  page.on("console", (msg: ConsoleMessage) => {
    if (msg.type() === "error") messages.push(`[console.error] ${msg.text()}`);
  });
  return { messages };
}

for (const theme of THEMES) {
  for (const { name: viewportName, viewport } of DESKTOP_MOBILE) {
    test.describe(`${theme} · ${viewportName}`, () => {
      test.use({ viewport, colorScheme: theme });

      test.beforeEach(async ({ page }) => {
        // Seed the theme key before the first navigation so ThemeInit reads it.
        await page.addInitScript(
          ([key, value]) => {
            try {
              localStorage.setItem(key, value);
            } catch {
              /* storage blocked; OS colorScheme still applies */
            }
          },
          [THEME_STORAGE_KEY, theme] as const,
        );
      });

      // One test per route. `auditRoutes` is filled in beforeAll; Playwright
      // resolves the parametrization at collection time, so we loop over the
      // static AUDIT route NAMES (known at collection time) and resolve each
      // name to its live path inside the test, keeping the EN book-33 slug
      // dynamic without a second source of truth.
      for (const base of AUDIT_ROUTE_NAMES) {
        test(base, async ({ page }) => {
          const route = auditRoutes.find((r) => r.name === base);
          test.skip(!route, `route ${base} was dropped (not present on the running site)`);
          if (!route) return;

          const { messages } = collectErrors(page);
          // route.path is relative (incl. the probed EN slug); the context
          // baseURL resolves it, matching the convention in smoke.spec.ts.
          const response = await page.goto(route.path, { waitUntil: "networkidle" });
          expect(response?.status() ?? 0, `navigation to ${route.path}`).toBeLessThan(400);
          await waitForFontsReady(page);
          const settle = settleMsFor(route.path);
          if (settle > 0) await page.waitForTimeout(settle);

          if (viewportName === "mobile") {
            const metrics = await page.evaluate(() => ({
              scrollWidth: Math.max(
                document.documentElement.scrollWidth,
                document.body.scrollWidth,
              ),
              clientWidth: document.documentElement.clientWidth,
            }));
            expect(
              overflows(metrics),
              `horizontal overflow on ${route.path} @ ${viewport.width}px: ` +
                `scrollWidth=${metrics.scrollWidth} clientWidth=${metrics.clientWidth}`,
            ).toBe(false);
          }

          expect(messages, `console errors on ${route.path} (${theme}/${viewportName}):\n${messages.join("\n")}`)
            .toEqual([]);
        });
      }
    });
  }
}
