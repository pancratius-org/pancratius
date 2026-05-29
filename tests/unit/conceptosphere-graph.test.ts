import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { GraphDataStore, type GraphCommunity, type GraphData, type GraphDataConfig, type GraphEdgeData, type GraphNodeData } from "../../src/components/conceptosphere/graph-data.ts";
import { applyGraphHighlighting } from "../../src/components/conceptosphere/graph-highlight.ts";
import { buildGraphModel, type ConceptRenderer } from "../../src/components/conceptosphere/graph-model.ts";
import { parseGraphPayload, type GraphPayload } from "../../src/components/conceptosphere/graph-payload.ts";
import type { GraphTheme } from "../../src/components/conceptosphere/graph-theme.ts";
import type { ConceptosphereMode } from "../../src/components/conceptosphere/graph-types.ts";
import { GraphInteractionState } from "../../src/components/conceptosphere/interaction-state.ts";

const THEME: GraphTheme = {
  isLight: false,
  dimNode: "rgba(70, 65, 55, 0.45)",
  dimEdge: "rgba(80, 70, 55, 0.04)",
  focusEdge: "rgba(233, 161, 66, 0.55)",
  defaultEdgeColor: "rgba(232, 227, 214, 0.10)",
  labelColor: "#f3eee0",
  labelHalo: "rgba(6, 8, 12, 0.55)",
  badgeHalo: "rgba(6, 8, 12, 0.75)",
  badgeInk: "#f3eee0",
  calloutBg: "rgba(9, 11, 16, 0.96)",
  calloutInk: "#f3eee0",
  focusRing: "rgba(233, 161, 66, 0.96)",
  focusRingMuted: "rgba(233, 161, 66, 0.82)",
  focusRingSoft: "rgba(233, 161, 66, 0.30)",
  focusRingMutedSoft: "rgba(233, 161, 66, 0.18)",
  focusCalloutBorder: "rgba(233, 161, 66, 0.72)",
  focusCalloutBorderMuted: "rgba(233, 161, 66, 0.50)",
  edgeInkRgb: [26, 18, 12],
  edgeNeutralRgb: [120, 110, 95],
  hullFillOpacity: "0.08",
  hullStrokeOpacity: "0.22",
  hullDimFillOpacity: "0.012",
  hullDimStrokeOpacity: "0.045",
};

describe("conceptosphere graph payload", () => {
  test("rejects malformed payload records at the public JSON boundary", () => {
    assert.throws(
      () => parseGraphPayload({ communities: [], nodes: [], edges: [{ source: "a", target: "b" }] }, "/graph.json"),
      /Invalid graph edge\[0] in \/graph\.json/,
    );
  });
});

describe("conceptosphere graph data", () => {
  test("normalizes book payloads with localized titles and present-only metrics", async () => {
    const restoreFetch = stubJsonFetch((): GraphPayload => ({
      stats: { node_count: 1 },
      communities: [{ id: 1, label: "Library", size: 1 }],
      nodes: [
        {
          id: "ru-book",
          slug: "ru-book",
          title: "Raw title",
          label: "Raw label",
          number: 7,
          community: 1,
          top_concepts: [{ label: "Faith", count: 3 }],
          top_books: [{ slug: "ru-book", title: "Raw title", count: 2 }],
          top_similar: [{ kind: "book", slug: "ru-book", title: "Raw title", weight: 0.9 }],
          top_similar_embed: [{ kind: "project", slug: "project", title: "Project", weight: 0.3 }],
        },
      ],
      edges: [],
    }));

    try {
      const data = await new GraphDataStore(pageConfig()).load("books");
      const [node] = data.nodes;
      assert.ok(node);

      assert.equal(node.label, "Localized title");
      assert.equal(node.title, "Localized title");
      assert.equal(node.bookNumberBadge, "7");
      assert.equal(node.relations.topBooks[0]?.title, "Localized title");
      assert.equal(node.relations.similarByConcepts[0]?.title, "Localized title");
      assert.equal(node.relations.similarByMeaning[0]?.title, "Project");
      assert.deepEqual(node.metrics, {});
      assert.equal(Object.hasOwn(node.metrics, "frequency"), false);
      assert.equal(data.communities.sortedBySize[0]?.label, "Library");
    } finally {
      restoreFetch();
    }
  });

  test("fails fast when a node references an unknown community", async () => {
    const restoreFetch = stubJsonFetch((): GraphPayload => ({
      stats: {},
      communities: [{ id: 1, label: "Known", size: 1 }],
      nodes: [{ id: "orphan", label: "Orphan", community: 9 }],
      edges: [],
    }));

    try {
      await assert.rejects(
        () => new GraphDataStore(pageConfig()).load("concepts"),
        /node "orphan" references unknown community 9/,
      );
    } finally {
      restoreFetch();
    }
  });
});

describe("conceptosphere graph model", () => {
  test("keeps only the strongest cross-community concept edges visible", () => {
    const { graph } = buildGraphModel(
      graphData({
        mode: "concepts",
        nodes: [
          conceptNode("a1", 1, 0.9),
          conceptNode("a2", 1, 0.7),
          conceptNode("b1", 2, 0.8),
          conceptNode("b2", 2, 0.6),
        ],
        edges: [
          edge("a1", "a2", 1.0),
          edge("a1", "b1", 0.1, 0.1),
          edge("a1", "b2", 0.8, 0.8),
          edge("a2", "b1", 0.4, 0.4),
          edge("a2", "b2", 0.2, 0.2),
        ],
      }),
      THEME,
      true,
    );

    assert.equal(graph.getEdgeAttribute("a1", "a2", "visibleBackbone"), true);
    assert.equal(graph.getEdgeAttribute("a1", "b2", "visibleBackbone"), true);
    assert.equal(graph.getEdgeAttribute("a2", "b1", "visibleBackbone"), true);
    assert.equal(graph.getEdgeAttribute("a2", "b2", "visibleBackbone"), true);
    assert.equal(graph.getEdgeAttribute("a1", "b1", "visibleBackbone"), false);
    assert.equal(graph.getEdgeAttribute("a1", "b1", "hidden"), true);
  });

  test("lays out a finite graph centered around the graph origin", () => {
    const { graph } = buildGraphModel(
      graphData({
        mode: "books",
        nodes: [
          bookNode("book-a", 1, 0.8, 1),
          bookNode("book-b", 1, 0.4, 2),
          bookNode("book-c", 2, 0.5, 3),
        ],
        edges: [
          edge("book-a", "book-b", 0.8),
          edge("book-b", "book-c", 0.4),
        ],
      }),
      THEME,
      true,
    );

    const xs = graph.nodes().map((node) => graph.getNodeAttribute(node, "x"));
    const ys = graph.nodes().map((node) => graph.getNodeAttribute(node, "y"));
    assert.equal(xs.every(Number.isFinite), true);
    assert.equal(ys.every(Number.isFinite), true);
    assert.ok(Math.abs((Math.min(...xs) + Math.max(...xs)) / 2) < 1e-6);
    assert.ok(Math.abs((Math.min(...ys) + Math.max(...ys)) / 2) < 1e-6);
    assert.equal(graph.getNodeAttribute("book-a", "bookNumberBadge"), "1");
  });
});

describe("conceptosphere graph interactions", () => {
  test("snapshots are immutable views of focus, filters, and search", () => {
    const state = new GraphInteractionState();
    state.pin("alpha");
    state.toggleCommunity(2);
    state.setSearch("  mercy  ");

    const snapshot = state.snapshot();
    state.clearAll();

    assert.deepEqual(snapshot, {
      hovered: "alpha",
      pinned: "alpha",
      filteredCommunities: new Set([2]),
      search: "mercy",
    });
    assert.deepEqual(state.snapshot(), {
      hovered: null,
      pinned: null,
      filteredCommunities: new Set(),
      search: "",
    });
  });

  test("highlight reducers dim search misses and keep matching edges visible", () => {
    const { graph } = buildGraphModel(
      graphData({
        mode: "concepts",
        nodes: [
          { ...conceptNode("faith", 1, 0.9), relations: { ...emptyRelations(), topBooks: [{ slug: "book-a", title: "Mercy Book", count: 1 }] } },
          conceptNode("silence", 2, 0.5),
        ],
        edges: [edge("faith", "silence", 0.5)],
      }),
      THEME,
      true,
    );
    const state = new GraphInteractionState();
    state.setSearch("mercy");
    const renderer = createReducerProbe();
    const hulls = createHullProbe([1, 2]);

    applyGraphHighlighting({
      mode: "concepts",
      graph,
      renderer: renderer.asRenderer(),
      theme: THEME,
      hulls: hulls.asSvg(),
      state: state.snapshot(),
    });

    const faith = renderer.reduceNode("faith", graph.getNodeAttributes("faith"));
    const silence = renderer.reduceNode("silence", graph.getNodeAttributes("silence"));
    const graphEdge = graph.edge("faith", "silence");
    assert.ok(graphEdge);

    assert.equal(faith.dimmed, false);
    assert.equal(faith.forceLabel, true);
    assert.equal(silence.dimmed, true);
    assert.equal(silence.color, THEME.dimNode);
    assert.equal(renderer.reduceEdge(graphEdge, graph.getEdgeAttributes(graphEdge)).hidden, false);
    assert.equal(hulls.opacityFor(1, "fill-opacity"), THEME.hullFillOpacity);
    assert.equal(hulls.opacityFor(2, "fill-opacity"), THEME.hullFillOpacity);
    assert.equal(renderer.refreshCount, 1);
  });

  test("highlight reducers hide edges outside the selected communities", () => {
    const { graph } = buildGraphModel(
      graphData({
        mode: "concepts",
        nodes: [
          conceptNode("faith", 1, 0.9),
          conceptNode("hope", 1, 0.7),
          conceptNode("silence", 2, 0.5),
        ],
        edges: [
          edge("faith", "hope", 0.8),
          edge("faith", "silence", 0.5),
        ],
      }),
      THEME,
      true,
    );
    const state = new GraphInteractionState();
    state.toggleCommunity(1);
    const renderer = createReducerProbe();
    const hulls = createHullProbe([1, 2]);

    applyGraphHighlighting({
      mode: "concepts",
      graph,
      renderer: renderer.asRenderer(),
      theme: THEME,
      hulls: hulls.asSvg(),
      state: state.snapshot(),
    });

    const selectedEdge = graph.edge("faith", "hope");
    const crossEdge = graph.edge("faith", "silence");
    assert.ok(selectedEdge);
    assert.ok(crossEdge);

    assert.equal(renderer.reduceEdge(selectedEdge, graph.getEdgeAttributes(selectedEdge)).hidden, false);
    assert.equal(renderer.reduceEdge(selectedEdge, graph.getEdgeAttributes(selectedEdge)).zIndex, 1);
    assert.equal(renderer.reduceEdge(crossEdge, graph.getEdgeAttributes(crossEdge)).hidden, true);
    assert.equal(hulls.opacityFor(1, "fill-opacity"), THEME.hullFillOpacity);
    assert.equal(hulls.opacityFor(2, "fill-opacity"), THEME.hullDimFillOpacity);
  });
});

function stubJsonFetch(handler: (url: string) => GraphPayload): () => void {
  const original = globalThis.fetch;
  globalThis.fetch = (...args: Parameters<typeof fetch>): Promise<Response> => {
    const input = args[0];
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    return Promise.resolve(
      new Response(JSON.stringify(handler(url)), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
  };
  return () => {
    globalThis.fetch = original;
  };
}

function pageConfig(): GraphDataConfig {
  return {
    conceptsUrl: "/concepts.json",
    booksUrl: "/books.json",
    bookSlugInfo: { "ru-book": { title: "Localized title" } },
  };
}

function graphData(input: {
  mode: ConceptosphereMode;
  nodes: GraphNodeData[];
  edges: GraphEdgeData[];
}): GraphData {
  const communities: GraphCommunity[] = [
    { id: 1, label: "One", size: input.nodes.filter((node) => node.communityId === 1).length, color: "#a33", rgb: [170, 51, 51] },
    { id: 2, label: "Two", size: input.nodes.filter((node) => node.communityId === 2).length, color: "#3a3", rgb: [51, 170, 51] },
  ];
  const nodesByCommunity = new Map<number, readonly GraphNodeData[]>(
    communities.map((community) => [
      community.id,
      input.nodes
        .filter((node) => node.communityId === community.id)
        .sort((a, b) => (b.metrics.centrality ?? 0) - (a.metrics.centrality ?? 0)),
    ]),
  );

  return {
    mode: input.mode,
    stats: {},
    communities: {
      all: communities,
      sortedBySize: [...communities].sort((a, b) => b.size - a.size),
      byId: new Map(communities.map((community) => [community.id, community])),
      nodesByCommunity,
    },
    nodes: input.nodes,
    edges: input.edges,
  };
}

function conceptNode(id: string, communityId: number, centrality: number): GraphNodeData {
  return {
    id,
    communityId,
    label: id,
    tags: [],
    metrics: { centrality, frequency: 8 },
    relations: emptyRelations(),
  };
}

function bookNode(id: string, communityId: number, centrality: number, number: number): GraphNodeData {
  return {
    id,
    communityId,
    label: id,
    title: id,
    bookNumberBadge: String(number),
    number,
    slug: id,
    tags: [],
    metrics: { centrality, frequency: 8 },
    relations: emptyRelations(),
  };
}

function emptyRelations(): GraphNodeData["relations"] {
  return {
    topBooks: [],
    topConcepts: [],
    similarByConcepts: [],
    similarByMeaning: [],
  };
}

function edge(source: string, target: string, weight: number, npmi?: number): GraphEdgeData {
  return {
    source,
    target,
    weight,
    ...(npmi !== undefined ? { npmi } : {}),
  };
}

interface ReducerProbe {
  readonly refreshCount: number;
  asRenderer(): ConceptRenderer;
  reduceNode(node: string, data: unknown): Record<string, unknown>;
  reduceEdge(edgeId: string, data: unknown): Record<string, unknown>;
}

function createReducerProbe(): ReducerProbe {
  let refreshCount = 0;
  let nodeReducer: ((node: string, data: unknown) => unknown) | null = null;
  let edgeReducer: ((edge: string, data: unknown) => unknown) | null = null;

  return {
    get refreshCount() {
      return refreshCount;
    },
    asRenderer() {
      return {
        setSetting: (key: string, value: unknown) => {
          if (key === "nodeReducer") nodeReducer = value as typeof nodeReducer;
          if (key === "edgeReducer") edgeReducer = value as typeof edgeReducer;
        },
        refresh: () => {
          refreshCount++;
        },
      } as unknown as ConceptRenderer;
    },
    reduceNode(node: string, data: unknown) {
      assert.ok(nodeReducer);
      return nodeReducer(node, data) as Record<string, unknown>;
    },
    reduceEdge(edgeId: string, data: unknown) {
      assert.ok(edgeReducer);
      return edgeReducer(edgeId, data) as Record<string, unknown>;
    },
  };
}

interface HullProbe {
  asSvg(): SVGSVGElement;
  opacityFor(communityId: number, name: string): string | null;
}

interface ProbePath {
  readonly communityId: number;
  getAttribute(name: string): string | null;
  setAttribute(name: string, value: string): void;
}

function createHullProbe(communities: readonly number[]): HullProbe {
  const paths = communities.map(createProbePath);

  return {
    asSvg() {
      return {
        querySelectorAll: (selector: string) => selector === "path" ? paths : [],
      } as unknown as SVGSVGElement;
    },
    opacityFor(communityId: number, name: string) {
      const path = paths.find((item) => item.communityId === communityId);
      return path?.getAttribute(name) ?? null;
    },
  };
}

function createProbePath(communityId: number): ProbePath {
  const attrs = new Map<string, string>([["data-com", String(communityId)]]);
  return {
    communityId,
    getAttribute(name: string) {
      return attrs.get(name) ?? null;
    },
    setAttribute(name: string, value: string) {
      attrs.set(name, value);
    },
  };
}
