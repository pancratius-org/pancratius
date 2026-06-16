import { defineConfig, devices } from "@playwright/test";

// Smoke runs against `npm run preview` (Astro's static preview server).
// We rely on `dist/` already being built — both `npm run build` and CI's
// equivalent pipeline produce `dist/` before tests run, so the webServer
// here just serves the existing artefact.
//
// `webServer.reuseExistingServer` honours an already-running preview
// (useful in local dev: `npm run preview` in one terminal, `npx
// playwright test` in another), and otherwise spawns one.

const PORT = Number(process.env.PORT ?? 4321);

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 800 } },
    },
  ],
  webServer: {
    command: `npx astro preview --port ${PORT} --host 127.0.0.1`,
    url: `http://127.0.0.1:${PORT}/`,
    reuseExistingServer: !process.env.CI && !process.env.PW_GATE,  // PW_GATE (npm run verify) forces a fresh preview on its own port
    timeout: 120_000,
  },
});
