import assert from "node:assert/strict";
import { test } from "node:test";

import config from "../../playwright.config.ts";

test("Playwright owns the preview server process", () => {
  const webServer = config.webServer;

  assert.ok(webServer && !Array.isArray(webServer));
  assert.equal(webServer.env?.ASTRO_PREVIEW_BACKGROUND, "1");
});
