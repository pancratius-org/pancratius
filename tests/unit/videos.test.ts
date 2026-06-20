import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { formatDuration, localizedEmbedUrl } from "../../src/lib/video-format.ts";

describe("formatDuration", () => {
  test("formats minutes:seconds for short clips", () => {
    assert.equal(formatDuration("PT8M42S"), "8:42");
    assert.equal(formatDuration("PT45S"), "0:45");
    assert.equal(formatDuration("PT4M"), "4:00");
  });

  test("formats hours:minutes:seconds for long talks", () => {
    assert.equal(formatDuration("PT1H3M"), "1:03:00");
    assert.equal(formatDuration("PT2H5M30S"), "2:05:30");
  });

  test("returns the raw string when the input is not parseable", () => {
    assert.equal(formatDuration("not-a-duration"), "not-a-duration");
  });
});

describe("localizedEmbedUrl", () => {
  const BASE = "https://www.youtube-nocookie.com/embed/abc123XYZ";

  test("sets only hl when no caption language is requested", () => {
    const u = new URL(localizedEmbedUrl(BASE, "ru", null));
    assert.equal(u.searchParams.get("hl"), "ru");
    assert.equal(u.searchParams.get("cc_lang_pref"), null);
    assert.equal(u.searchParams.get("cc_load_policy"), null);
  });

  test("forces the requested caption track on", () => {
    const u = new URL(localizedEmbedUrl(BASE, "en", "en"));
    assert.equal(u.searchParams.get("hl"), "en");
    assert.equal(u.searchParams.get("cc_lang_pref"), "en");
    assert.equal(u.searchParams.get("cc_load_policy"), "1");
  });

  test("merges with an embed URL that already carries a query", () => {
    const u = new URL(localizedEmbedUrl(`${BASE}?start=30`, "en", "en"));
    assert.equal(u.searchParams.get("start"), "30");
    assert.equal(u.searchParams.get("hl"), "en");
  });

  test("throws when the embed URL violates the URL invariant", () => {
    assert.throws(() => localizedEmbedUrl("not a url", "en", "en"), TypeError);
  });
});
