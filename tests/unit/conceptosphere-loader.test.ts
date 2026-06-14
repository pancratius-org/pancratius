import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, test } from "node:test";

import { graphPayloadPath } from "../../src/lib/conceptosphere-payload-path.ts";
import { joinLocalePayload, type Graph, type Overlay } from "../../build/copy-graph-payloads.ts";

// The honesty contract (conceptosphere-bilingual-design.md §2): the EN
// conceptosphere is the RU topology joined with the authored overlay at BUILD
// time, and that join is the ONLY bridge. The server-rendered mobile list and
// the desktop graph must read the SAME per-locale payload, so /en/ never shows
// Russian concept/community labels under an English URL. These tests pin the
// loader's locale routing: RU reads the source graph; EN reads the joined
// payload the build emitted, and a missing EN payload fails loud (no silent RU
// fallback).

describe("graphPayloadPath locale routing", () => {
  let root: string;

  beforeEach(() => {
    root = mkdtempSync(join(tmpdir(), "concepto-loader-"));
    mkdirSync(join(root, "data"), { recursive: true });
    mkdirSync(join(root, "public", "data"), { recursive: true });
  });

  afterEach(() => {
    rmSync(root, { recursive: true, force: true });
  });

  test("RU (default) resolves the un-suffixed source under data/", () => {
    assert.equal(
      graphPayloadPath("pancratius-concepts-graph", "ru", root),
      join(root, "data", "pancratius-concepts-graph.json"),
    );
  });

  test("EN resolves the per-locale join under public/data/ when present", () => {
    const en = join(root, "public", "data", "pancratius-concepts-graph.en.json");
    writeFileSync(en, "{}");
    assert.equal(graphPayloadPath("pancratius-concepts-graph", "en", root), en);
  });

  test("EN throws fail-loud when the joined payload is absent (no RU fallback)", () => {
    // Even with a RU source graph present, EN must NOT fall back to it.
    writeFileSync(join(root, "data", "pancratius-concepts-graph.json"), "{}");
    assert.throws(
      () => graphPayloadPath("pancratius-concepts-graph", "en", root),
      /pancratius-concepts-graph\.en\.json is missing.*not a silent RU fallback/s,
    );
  });

  test("the EN payload the loader points at carries OVERLAY labels, not RU ones", () => {
    // Mirror the build: RU source graph in data/, the joined EN payload in
    // public/data/. The loader's EN path must surface the English labels.
    const ruGraph: Graph = {
      communities: [{ id: 0, key: "c0ffee01", label: "Свет" }],
      nodes: [{ id: "свет", concept_id: "свет", label: "Свет", lemma: "свет", community: 0 }],
      edges: [],
    };
    const overlay: Overlay = { "concept:свет": { label: "Light" }, "community:c0ffee01": { label: "Light & Darkness" } };

    writeFileSync(join(root, "data", "pancratius-concepts-graph.json"), JSON.stringify(ruGraph));
    const joined = joinLocalePayload(ruGraph, overlay, true);
    const enPath = join(root, "public", "data", "pancratius-concepts-graph.en.json");
    writeFileSync(enPath, JSON.stringify(joined));

    // RU path → Russian label; EN path → English label, from the same topology.
    const ru = JSON.parse(
      readFileSync(graphPayloadPath("pancratius-concepts-graph", "ru", root), "utf-8"),
    ) as Graph;
    const en = JSON.parse(
      readFileSync(graphPayloadPath("pancratius-concepts-graph", "en", root), "utf-8"),
    ) as Graph;
    assert.equal(ru.nodes?.[0]?.label, "Свет");
    assert.equal(en.nodes?.[0]?.label, "Light");
    assert.equal(en.communities?.[0]?.label, "Light & Darkness");
  });
});
