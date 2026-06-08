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

  test("reports unitless line-height and declarations packed onto one line", () => {
    const files = new Map([
      [
        "src/a.astro",
        [
          "<style>",
          ".a { font-size: 1.5rem; line-height: 0.96; }",
          ".b { line-height: 0.96; }",
          ".c { line-height: 0.96; }",
          "</style>",
        ].join("\n"),
      ],
    ]);
    const ctx = makeContext("/unused");
    const report = analyzeCssValues({
      ...ctx,
      walk: () => [...files.keys()],
      read: (file) => files.get(file) ?? "",
    }, { minCount: 1, limit: 10, examples: 4 });

    // Unitless leading must now be visible to the typography report.
    assert.ok(report.typography.some((group) => group.value === "0.96" && group.count === 3));
    // A declaration packed inline next to another on one line is still parsed.
    assert.ok(report.typography.some((group) => group.value === "1.5rem"));
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
      roleDrift: [],
    });

    assert.match(output, /CSS value diagnostic/);
    assert.match(output, /Layout literals/);
    assert.match(output, /Typography role drift\n {2}none/);
  });
});

describe("role drift", () => {
  test("flags a raw clamp that duplicates a typography-role token, not local values", () => {
    const files = new Map([
      ["src/styles/typography.css", ":root { --type-x-size: clamp(3rem, 7.4vw, 5.4rem); }"],
      ["src/components/A.astro", "<style>.a { font-size: clamp(3rem, 7.4vw, 5.4rem); }</style>"],
      // honest local value that must NOT be flagged
      ["src/components/B.astro", "<style>.b { font-size: 0.98; line-height: 0.98; }</style>"],
    ]);
    const ctx = makeContext("/unused");
    const report = analyzeCssValues({
      ...ctx,
      walk: () => [...files.keys()],
      read: (file) => files.get(file) ?? "",
      exists: (file) => files.has(file),
    }, { minCount: 1, limit: 10, examples: 4 });

    assert.equal(report.roleDrift.length, 1);
    const [drift] = report.roleDrift;
    if (drift === undefined) throw new Error("expected one drift group");
    assert.equal(drift.value, "clamp(3rem, 7.4vw, 5.4rem)");
    assert.equal(drift.token, "--type-x-size");
    assert.equal(drift.uses[0]?.file, "src/components/A.astro");
    // the token definition itself is not drift
    assert.ok(drift.uses.every((u) => u.file !== "src/styles/typography.css"));
  });
});
