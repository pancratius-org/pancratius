import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";

import {
  ANALYTICS_EVENT_NAMES,
  LINK_EVENT_NAMES,
  progressMarksBetween,
  trackAttributeData,
} from "../../src/lib/analytics.ts";

const EVENT_NAMES: readonly string[] = ANALYTICS_EVENT_NAMES;
const LINK_NAMES = LINK_EVENT_NAMES as ReadonlySet<string>;

test("anchors never use Umami's interceptor", () => {
  // The tracker handles `data-umami-event` clicks by re-navigating via
  // `location.href`, which drops `download`-attribute semantics. Anchors go
  // through the delegated `data-track-event` listener instead.
  for (const file of sourceFiles(join(process.cwd(), "src"))) {
    assert.doesNotMatch(readFileSync(file, "utf8"), /<a\b[^>]*data-umami-event/, file);
  }
});

test("markup event names stay within the vocabulary", () => {
  for (const file of sourceFiles(join(process.cwd(), "src"))) {
    const source = readFileSync(file, "utf8");
    for (const [, name = ""] of source.matchAll(/data-umami-event="([^"]*)"/g)) {
      assert.ok(EVENT_NAMES.includes(name), `${file}: unknown event "${name}"`);
    }
    for (const [, name = ""] of source.matchAll(/data-track-event="([^"]*)"/g)) {
      assert.ok(LINK_NAMES.has(name), `${file}: "${name}" is not a link event`);
    }
  }
});

test("trackAttributeData lifts data-track-* into event data", () => {
  assert.deepEqual(
    trackAttributeData({
      trackEvent: "work-download",
      trackKind: "book",
      trackSlug: "01-evangelie-tsarstviya",
      trackLocale: "ru",
      trackFormat: "pdf",
    }),
    { kind: "book", slug: "01-evangelie-tsarstviya", locale: "ru", format: "pdf" },
  );
});

test("progressMarksBetween yields milestones crossed by scrolling, once", () => {
  assert.deepEqual(progressMarksBetween(0, 0.6), ["25", "50"]);
  assert.deepEqual(progressMarksBetween(0.6, 0.8), ["75"]);
  // A landing position at 0.55 already covers 25/50 — they never fire.
  assert.deepEqual(progressMarksBetween(0.55, 0.7), []);
  // Sub-viewport works land at ratio 1: no progress noise at all.
  assert.deepEqual(progressMarksBetween(1, 1), []);
  assert.deepEqual(progressMarksBetween(0.25, 0.25), []);
});

test("trackAttributeData drops the read marker itself", () => {
  assert.deepEqual(
    trackAttributeData({ trackRead: "", trackKind: "poem", trackSlug: "x", trackLocale: "en" }),
    { kind: "poem", slug: "x", locale: "en" },
  );
});

test("trackAttributeData ignores foreign attributes and undefined values", () => {
  assert.deepEqual(
    trackAttributeData({ trackChannel: "telegram", pagefindBody: "", copyLabel: "x", trackMissing: undefined }),
    { channel: "telegram" },
  );
});

function sourceFiles(dir: string): string[] {
  const files: string[] = [];
  for (const dirent of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, dirent.name);
    if (dirent.isDirectory()) {
      files.push(...sourceFiles(path));
      continue;
    }
    if (dirent.isFile() && /\.(astro|ts)$/.test(dirent.name)) files.push(path);
  }
  return files;
}
