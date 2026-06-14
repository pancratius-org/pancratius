import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { joinLocalePayload, type Graph, type Overlay } from "../../build/copy-graph-payloads.ts";

const OVERLAY: Overlay = {
  "concept:свет": { label: "Light", gloss: "Divine light." },
  "concept:тьма": { label: "Darkness" },
  "community:c0ffee01": { label: "Light & Darkness" },
};

function conceptsGraph(): Graph {
  return {
    generated_at: "x",
    communities: [{ id: 0, key: "c0ffee01", label: "Свет", color_index: 0 }],
    nodes: [
      { id: "свет", concept_id: "свет", label: "Свет", lemma: "свет", community: 0 },
      { id: "тьма", concept_id: "тьма", label: "Тьма", lemma: "тьма", community: 0 },
    ],
    edges: [{ source: "свет", target: "тьма", weight: 3 }],
  };
}

describe("joinLocalePayload", () => {
  test("substitutes EN concept labels by stable id", () => {
    const out = joinLocalePayload(conceptsGraph(), OVERLAY, true);
    const labels = (out.nodes ?? []).map((n) => n.label);
    assert.deepEqual(labels, ["Light", "Darkness"]);
  });

  test("attaches gloss when present, omits it when absent", () => {
    const out = joinLocalePayload(conceptsGraph(), OVERLAY, true);
    const [light, darkness] = out.nodes ?? [];
    assert.equal(light?.gloss, "Divine light.");
    assert.equal("gloss" in (darkness ?? {}), false);
  });

  test("substitutes community labels by fingerprint key", () => {
    const out = joinLocalePayload(conceptsGraph(), OVERLAY, true);
    assert.equal(out.communities?.[0]?.label, "Light & Darkness");
  });

  test("keeps language-invariant topology untouched (edges, ids, lemma, key)", () => {
    const out = joinLocalePayload(conceptsGraph(), OVERLAY, true);
    assert.deepEqual(out.edges, [{ source: "свет", target: "тьма", weight: 3 }]);
    const light = out.nodes?.[0];
    assert.ok(light);
    assert.equal(light.id, "свет");
    assert.equal(light.lemma, "свет");
    assert.equal(out.communities?.[0]?.key, "c0ffee01");
  });

  test("does not mutate the input graph", () => {
    const input = conceptsGraph();
    joinLocalePayload(input, OVERLAY, true);
    assert.equal(input.nodes?.[0]?.label, "Свет");
    assert.equal(input.communities?.[0]?.label, "Свет");
  });

  test("falls back from concept_id to node id", () => {
    const graph: Graph = {
      nodes: [{ id: "свет", label: "Свет", lemma: "свет", community: 0 }],
      communities: [],
    };
    const out = joinLocalePayload(graph, OVERLAY, true);
    assert.equal(out.nodes?.[0]?.label, "Light");
  });

  test("throws on a missing concept translation (no silent RU fallback)", () => {
    const graph: Graph = {
      nodes: [{ id: "страх", concept_id: "страх", label: "Страх", community: 0 }],
      communities: [],
    };
    assert.throws(() => joinLocalePayload(graph, OVERLAY, true), /missing a label for concept "concept:страх"/);
  });

  test("throws on a missing community translation", () => {
    const graph: Graph = {
      nodes: [],
      communities: [{ id: 0, key: "deadbeef", label: "Страх" }],
    };
    assert.throws(() => joinLocalePayload(graph, OVERLAY, true), /missing a label for community "community:deadbeef"/);
  });

  test("does NOT translate book node titles, but DOES translate their top_concepts + communities", () => {
    const booksGraph: Graph = {
      mode: "books",
      communities: [{ id: 0, key: "c0ffee01", label: "Свет" }],
      nodes: [{
        id: "1-foo", slug: "1-foo", number: 1, label: "Книга", title: "Книга", community: 0,
        top_concepts: [
          { concept_id: "свет", label: "Свет", lemma: "свет", count: 10 },
          { concept_id: "тьма", label: "Тьма", lemma: "тьма", count: 5 },
        ],
      }],
      edges: [],
    };
    const out = joinLocalePayload(booksGraph, OVERLAY, false);
    // Book node title stays RU (it degrades via the badge, not translation here).
    const node = out.nodes[0];
    assert.equal(node.label, "Книга");
    // top_concepts labels ARE translated (same concept vocabulary).
    const tc = node.top_concepts as { label: string; lemma: string; count: number }[];
    assert.deepEqual(tc.map((c) => c.label), ["Light", "Darkness"]);
    // Topology under the ref (lemma, count) is preserved.
    assert.equal(tc[0].lemma, "свет");
    assert.equal(tc[0].count, 10);
    // Community label is translated.
    assert.equal(out.communities?.[0]?.label, "Light & Darkness");
  });

  test("throws on a book top-concept whose concept_id has no EN entry", () => {
    const booksGraph: Graph = {
      mode: "books",
      communities: [],
      nodes: [{
        id: "1-foo", slug: "1-foo", number: 1, label: "Книга", community: 0,
        top_concepts: [{ concept_id: "страх", label: "Страх", lemma: "страх", count: 3 }],
      }],
      edges: [],
    };
    assert.throws(
      () => joinLocalePayload(booksGraph, OVERLAY, false),
      /missing a label for book top concept "concept:страх"/,
    );
  });

  test("leaves a book top-concept without concept_id untouched (pre-regen refs)", () => {
    const booksGraph: Graph = {
      mode: "books",
      communities: [],
      nodes: [{
        id: "1-foo", slug: "1-foo", number: 1, label: "Книга", community: 0,
        top_concepts: [{ label: "Свет", lemma: "свет", count: 7 }],
      }],
      edges: [],
    };
    const out = joinLocalePayload(booksGraph, OVERLAY, false);
    const tc = out.nodes?.[0]?.top_concepts as { label: string }[];
    assert.equal(tc[0]?.label, "Свет");
  });

  test("skips communities without a fingerprint key (pre-regen payloads)", () => {
    const graph: Graph = {
      nodes: [],
      communities: [{ id: 0, label: "Свет" }],
    };
    const out = joinLocalePayload(graph, OVERLAY, true);
    assert.equal(out.communities?.[0]?.label, "Свет");
  });
});
