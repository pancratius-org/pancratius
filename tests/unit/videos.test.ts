import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { formatDuration, layoutFor } from "../../src/lib/video-format.ts";

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

describe("layoutFor", () => {
  test("any heading promotes to blog", () => {
    assert.equal(layoutFor(1, ""), "blog");
  });

  test("empty body without headings is compact", () => {
    assert.equal(layoutFor(0, ""), "compact");
  });

  test("short prose without headings stays compact", () => {
    assert.equal(layoutFor(0, "A single short paragraph that does not reach the threshold."), "compact");
  });

  test("long prose without headings is blog", () => {
    const long = "Lorem ipsum dolor sit amet, consectetur adipiscing elit. ".repeat(20);
    assert.equal(layoutFor(0, long), "blog");
  });

  test("markdown syntax does not inflate the count", () => {
    const padded = "#".repeat(800);
    assert.equal(layoutFor(0, padded), "compact");
  });
});
