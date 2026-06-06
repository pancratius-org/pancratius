import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
  analyzeCssValues,
  extractCssBlocks,
  formatCssValueReport,
} from "../../audit/lib/css_values.ts";
import { makeContext } from "../../audit/lib/rule.ts";

describe("extractCssBlocks", () => {
  test("keeps Astro style block line offsets", () => {
    const blocks = extractCssBlocks("src/demo.astro", [
      "---",
      "const x = 1;",
      "---",
      "<style>",
      ".demo { max-width: 42rem; }",
      "</style>",
    ].join("\n"));

    const [block] = blocks;
    if (block === undefined) throw new Error("expected one style block");
    assert.equal(block.lineOffset, 3);
    assert.match(block.css, /42rem/);
  });
});

describe("analyzeCssValues", () => {
  test("groups repeated values and same-shaped literal families", () => {
    const files = new Map([
      [
        "src/a.css",
        [
          ".a { max-width: 42rem; padding-block: clamp(2rem, 5vw, 4rem); font-size: 1.1rem; }",
          ".b { max-inline-size: 42rem; padding: clamp(2rem, 5vw, 4rem); letter-spacing: -0.01em; }",
        ].join("\n"),
      ],
      [
        "src/b.astro",
        [
          "<style>",
          ".c { width: 320px; margin-block: clamp(2rem, 5vw, 4rem); font-size: 1.1rem; }",
          "</style>",
        ].join("\n"),
      ],
    ]);
    const ctx = makeContext("/unused");
    const report = analyzeCssValues({
      ...ctx,
      walk: () => [...files.keys()],
      read: (file) => files.get(file) ?? "",
    }, { minCount: 2, limit: 10, examples: 2 });

    assert.equal(report.files, 2);
    assert.equal(report.blocks, 2);
    assert.ok(report.repeated.some((group) => group.value === "42rem" && group.count === 2));
    assert.ok(report.layout.some((group) => group.value === "42rem" && group.count === 2));
    assert.ok(report.spacing.some((group) => group.value === "clamp(2rem, 5vw, 4rem)" && group.count === 3));
    assert.ok(report.typography.some((group) => group.value === "1.1rem" && group.count === 2));
    assert.ok(report.largePixels.some((group) => group.value === "320px" && group.count === 1));
  });
});

describe("formatCssValueReport", () => {
  test("prints a diagnostic report", () => {
    const output = formatCssValueReport({
      files: 1,
      blocks: 1,
      declarations: 1,
      repeated: [],
      layout: [],
      spacing: [],
      typography: [],
      largePixels: [],
    });

    assert.match(output, /CSS value diagnostic/);
    assert.match(output, /Layout literals/);
  });
});
