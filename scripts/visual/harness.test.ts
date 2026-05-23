// Unit tests for the pure helpers behind the visual scripts. Browser glue is not
// tested here — it has no logic worth mocking a browser for; the value is in the
// argument parsing, naming, and Lighthouse report extraction.
//
// Run: npm run test:unit  (= node --experimental-strip-types --test "scripts/visual/*.test.ts")

import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
  midScrollY,
  overflows,
  parseTag,
  screenshotName,
  settleMsFor,
} from "./harness.ts";
import {
  categoryScore,
  lighthouseArgs,
  lighthouseOutName,
  scoresTable,
  summarizeReport,
  type LighthouseReport,
  type Row,
} from "./lighthouse.ts";

describe("parseTag", () => {
  test("reads the =form", () => {
    assert.equal(parseTag(["--tag=after"], "before"), "after");
  });
  test("reads the space form (the form the old scripts documented but ignored)", () => {
    assert.equal(parseTag(["--tag", "after"], "before"), "after");
  });
  test("falls back when absent", () => {
    assert.equal(parseTag(["--other", "x"], "before"), "before");
  });
  test("falls back on an empty value", () => {
    assert.equal(parseTag(["--tag="], "before"), "before");
    assert.equal(parseTag(["--tag", ""], "before"), "before");
  });
  test("falls back when --tag is the last arg", () => {
    assert.equal(parseTag(["--tag"], "before"), "before");
  });
});

describe("screenshotName", () => {
  test("base form", () => {
    assert.equal(screenshotName("dark", "desktop", "home-ru"), "dark-desktop-home-ru.png");
  });
  test("with a variant", () => {
    assert.equal(screenshotName("light", "mobile", "books-ru", "mid"), "light-mobile-books-ru-mid.png");
  });
});

describe("midScrollY", () => {
  test("half the page for short pages", () => {
    assert.equal(midScrollY(1000), 500);
  });
  test("floors odd halves", () => {
    assert.equal(midScrollY(999), 499);
  });
  test("caps at 1200 for tall pages", () => {
    assert.equal(midScrollY(8000), 1200);
  });
  test("exactly at the cap boundary", () => {
    assert.equal(midScrollY(2400), 1200);
  });
});

describe("overflows", () => {
  test("flags more than a 1px difference", () => {
    assert.equal(overflows({ scrollWidth: 420, clientWidth: 375 }), true);
  });
  test("ignores a 1px rounding difference", () => {
    assert.equal(overflows({ scrollWidth: 376, clientWidth: 375 }), false);
  });
  test("no overflow when equal", () => {
    assert.equal(overflows({ scrollWidth: 375, clientWidth: 375 }), false);
  });
});

describe("settleMsFor", () => {
  test("conceptosphere needs a settle delay", () => {
    assert.equal(settleMsFor("/conceptosphere/"), 1200);
    assert.equal(settleMsFor("/en/conceptosphere/"), 1200);
  });
  test("other routes need none", () => {
    assert.equal(settleMsFor("/books/"), 0);
  });
});

describe("categoryScore", () => {
  test("scales 0–1 to 0–100", () => {
    assert.equal(categoryScore({ score: 0.95 }), 95);
  });
  test("rounds", () => {
    assert.equal(categoryScore({ score: 0.945 }), 95);
    assert.equal(categoryScore({ score: 0.944 }), 94);
  });
  test("treats null/undefined/missing as 0", () => {
    assert.equal(categoryScore({ score: null }), 0);
    assert.equal(categoryScore({}), 0);
    assert.equal(categoryScore(undefined), 0);
  });
});

describe("lighthouseArgs", () => {
  test("desktop adds the desktop preset", () => {
    const args = lighthouseArgs("http://localhost:4321/", "desktop", "/tmp/out.json");
    assert.ok(args.includes("--preset=desktop"));
  });
  test("mobile uses the default form factor (no preset)", () => {
    const args = lighthouseArgs("http://localhost:4321/", "mobile", "/tmp/out.json");
    assert.ok(!args.some((a) => a.startsWith("--preset=")));
  });
  test("always carries the url, output path, and pinned CLI", () => {
    const args = lighthouseArgs("http://localhost:4321/books/", "mobile", "/tmp/out.json");
    assert.ok(args.includes("http://localhost:4321/books/"));
    assert.ok(args.includes("--output-path=/tmp/out.json"));
    assert.ok(args.includes("lighthouse@13"));
  });
});

describe("lighthouseOutName", () => {
  test("composes viewport and name", () => {
    assert.equal(lighthouseOutName("mobile", "home-ru"), "mobile-home-ru.json");
  });
});

describe("summarizeReport", () => {
  test("extracts scores and diagnostics", () => {
    const report: LighthouseReport = {
      categories: {
        performance: { score: 0.9 },
        accessibility: { score: 1 },
        "best-practices": { score: 0.83 },
        seo: { score: 0.92 },
      },
      audits: {
        "largest-contentful-paint": { displayValue: "1.2 s" },
        "cumulative-layout-shift": { displayValue: "0.01" },
        "total-blocking-time": { displayValue: "30 ms" },
        "server-response-time": { displayValue: "120 ms" },
        "render-blocking-resources": {
          details: { items: [{ url: "a.css" }, { url: "b.css" }, { url: "c.css" }, { url: "d.css" }] },
        },
        "unused-javascript": { numericValue: 4096 },
        "uses-responsive-images": { details: { items: [{ url: "x.png" }, { url: "y.png" }] } },
      },
    };
    const { scores, diag } = summarizeReport(report);
    assert.deepEqual(scores, { perf: 90, a11y: 100, bp: 83, seo: 92 });
    assert.equal(diag.lcp, "1.2 s");
    assert.equal(diag.ttfb, "120 ms");
    assert.deepEqual(diag.renderBlocked, ["a.css", "b.css", "c.css"]); // capped at 3
    assert.equal(diag.unusedJsKb, 4); // 4096 / 1024
    assert.equal(diag.imgSizing, 2);
  });

  test("an empty report yields zeros and blanks, not a crash", () => {
    const { scores, diag } = summarizeReport({});
    assert.deepEqual(scores, { perf: 0, a11y: 0, bp: 0, seo: 0 });
    assert.equal(diag.lcp, "");
    assert.deepEqual(diag.renderBlocked, []);
    assert.equal(diag.unusedJsKb, 0);
    assert.equal(diag.imgSizing, 0);
  });
});

describe("scoresTable", () => {
  test("renders a Markdown table with header, separator, and one row per result", () => {
    const rows: Row[] = [
      {
        name: "home-ru",
        route: "/",
        viewport: "mobile",
        scores: { perf: 99, a11y: 100, bp: 96, seo: 100 },
        diag: { lcp: "1.0 s", cls: "0", tbt: "10 ms", ttfb: "80 ms", renderBlocked: [], unusedJsKb: 0, imgSizing: 0 },
      },
    ];
    const lines = scoresTable(rows).split("\n");
    assert.equal(lines.length, 3);
    assert.ok(lines[0].startsWith("| page | vp |"));
    assert.ok(lines[1].startsWith("|---|"));
    assert.ok(lines[2].includes("home-ru"));
    assert.ok(lines[2].includes("99"));
  });
});
