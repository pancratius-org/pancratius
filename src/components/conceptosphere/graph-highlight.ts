import type { EdgeDisplayData, NodeDisplayData } from "sigma/types";

import type {
  ConceptEdgeAttributes,
  ConceptGraph,
  ConceptNodeAttributes,
  ConceptRenderer,
} from "./graph-model.ts";
import type { GraphTheme } from "./graph-theme.ts";
import type { ConceptosphereMode } from "./graph-types.ts";
import type { InteractionSnapshot } from "./interaction-state.ts";

interface HighlightRequest {
  mode: ConceptosphereMode;
  graph: ConceptGraph;
  renderer: ConceptRenderer;
  theme: GraphTheme;
  hulls: SVGSVGElement;
  state: InteractionSnapshot;
}

interface HighlightContext extends HighlightRequest {
  focus: string | null;
  focusSet: Set<string> | null;
  hasFilter: boolean;
  hasSearch: boolean;
  searchQuery: string;
  showFocusNeighborLabels: boolean;
}

type NodeReducerOutput = Partial<NodeDisplayData> & {
  forceLabel?: boolean;
  dimmed?: boolean;
  baseSize?: number;
  communityId?: number;
  baseColor?: string;
};

type EdgeReducerOutput = Partial<EdgeDisplayData> & {
  visibleBackbone?: boolean;
  withinCommunity?: boolean;
  baseColor?: string;
  baseSize?: number;
};

type EdgeDisplayState =
  | "hidden"
  | "dimmedBackbone"
  | "focused"
  | "filteredCommunity"
  | "backbone";

interface EdgeEndpoints {
  sourceId: string;
  targetId: string;
  source: ConceptNodeAttributes;
  target: ConceptNodeAttributes;
}

export function applyGraphHighlighting(request: HighlightRequest): void {
  const context = highlightContext(request);
  request.renderer.setSetting("nodeReducer", (node, data) =>
    nodeDisplay(node, data, context));
  request.renderer.setSetting("edgeReducer", (edge, data) =>
    edgeDisplay(edge, data, context));
  updateHullOpacity(context);
  request.renderer.refresh();
}

function highlightContext(request: HighlightRequest): HighlightContext {
  const focus = request.state.pinned ?? request.state.hovered;
  const searchQuery = request.state.search.toLowerCase();
  return {
    ...request,
    focus,
    focusSet: focus ? neighborhood(request.graph, focus) : null,
    hasFilter: request.state.filteredCommunities.size > 0,
    hasSearch: searchQuery.length > 0,
    searchQuery,
    showFocusNeighborLabels: request.mode === "concepts" && request.state.pinned !== null,
  };
}

function neighborhood(graph: ConceptGraph, node: string): Set<string> {
  const set = new Set<string>([node]);
  graph.forEachNeighbor(node, (neighbor) => set.add(neighbor));
  return set;
}

function matchesSearch(attrs: ConceptNodeAttributes, context: HighlightContext): boolean {
  const query = context.searchQuery;
  if (!query) return true;
  if (attrs.label.toLowerCase().includes(query)) return true;
  if ((attrs.title ?? "").toLowerCase().includes(query)) return true;

  if (context.mode === "concepts") {
    return attrs.topBooks.some((book) =>
      book.title.toLowerCase().includes(query) || book.slug.toLowerCase().includes(query),
    );
  }

  return attrs.topConcepts.some((concept) =>
    (concept.label ?? "").toLowerCase().includes(query)
    || (concept.lemma ?? "").toLowerCase().includes(query),
  );
}

function nodeDisplay(
  node: string,
  data: ConceptNodeAttributes,
  context: HighlightContext,
): NodeReducerOutput {
  const out = baseNodeDisplay(node, data, context);
  const interaction = nodeInteractionState(node, context);
  return interaction ? highlightedNodeDisplay(out, data, interaction) : out;
}

function baseNodeDisplay(
  node: string,
  data: ConceptNodeAttributes,
  context: HighlightContext,
): NodeReducerOutput {
  const out: NodeReducerOutput = { ...data };
  if (isDimNode(data, context, node)) {
    out.color = context.theme.dimNode;
    out.forceLabel = false;
    out.zIndex = 0;
    out.dimmed = true;
  } else {
    out.color = data.baseColor;
    const focused = context.focusSet?.has(node) ?? false;
    out.zIndex = focused ? 2 : 1;
    out.forceLabel = forceNodeLabel(data, context, node, focused);
    out.dimmed = false;
  }
  return out;
}

type NodeInteractionState = "pinned" | "hovered";

function nodeInteractionState(node: string, context: HighlightContext): NodeInteractionState | null {
  if (context.state.pinned === node) return "pinned";
  if (context.state.hovered === node) return "hovered";
  return null;
}

function highlightedNodeDisplay(
  out: NodeReducerOutput,
  data: ConceptNodeAttributes,
  interaction: NodeInteractionState,
): NodeReducerOutput {
  out.color = data.baseColor;
  out.size = data.baseSize * (interaction === "pinned" ? 1.12 : 1.08);
  out.forceLabel = false;
  out.zIndex = interaction === "pinned" ? 4 : 3;
  out.dimmed = false;
  return out;
}

function isDimNode(data: ConceptNodeAttributes, context: HighlightContext, node: string): boolean {
  if (context.hasFilter && !context.state.filteredCommunities.has(data.communityId)) return true;
  if (context.hasSearch && !matchesSearch(data, context)) return true;
  return context.focusSet !== null && !context.focusSet.has(node);
}

function forceNodeLabel(
  data: ConceptNodeAttributes,
  context: HighlightContext,
  node: string,
  focused: boolean,
): boolean {
  if (context.focusSet !== null) {
    return context.showFocusNeighborLabels && node !== context.focus && focused;
  }
  if (context.hasFilter && context.state.filteredCommunities.has(data.communityId)) return true;
  return context.hasSearch && matchesSearch(data, context);
}

function edgeDisplay(
  edge: string,
  data: ConceptEdgeAttributes,
  context: HighlightContext,
): EdgeReducerOutput {
  const out: EdgeReducerOutput = { ...data };
  const endpoints = edgeEndpoints(edge, context);
  return applyEdgeDisplayState(
    out,
    data,
    context,
    edgeDisplayState(data, endpoints, context),
  );
}

function edgeEndpoints(edge: string, context: HighlightContext): EdgeEndpoints {
  const [sourceId, targetId] = context.graph.extremities(edge);
  return {
    sourceId,
    targetId,
    source: context.graph.getNodeAttributes(sourceId),
    target: context.graph.getNodeAttributes(targetId),
  };
}

function edgeDisplayState(
  data: ConceptEdgeAttributes,
  endpoints: EdgeEndpoints,
  context: HighlightContext,
): EdgeDisplayState {
  if (isDimEdge(endpoints, context)) return dimmedEdgeState(data, context);
  if (context.focusSet !== null) return "focused";
  if (isInsideFilteredCommunities(endpoints, context)) return "filteredCommunity";
  return data.visibleBackbone ? "backbone" : "hidden";
}

function dimmedEdgeState(data: ConceptEdgeAttributes, context: HighlightContext): EdgeDisplayState {
  if (hasActiveVisibilityConstraint(context)) return "hidden";
  return data.visibleBackbone ? "dimmedBackbone" : "hidden";
}

function hasActiveVisibilityConstraint(context: HighlightContext): boolean {
  return context.hasFilter || context.hasSearch || context.focusSet !== null;
}

function isInsideFilteredCommunities(
  endpoints: EdgeEndpoints,
  context: HighlightContext,
): boolean {
  return context.hasFilter
    && context.state.filteredCommunities.has(endpoints.source.communityId)
    && context.state.filteredCommunities.has(endpoints.target.communityId);
}

function applyEdgeDisplayState(
  out: EdgeReducerOutput,
  data: ConceptEdgeAttributes,
  context: HighlightContext,
  state: EdgeDisplayState,
): EdgeReducerOutput {
  if (state === "hidden") return hideEdge(out);
  if (state === "dimmedBackbone") return showEdge(out, context.theme.dimEdge, data.baseSize * 0.5);
  if (state === "focused") return showEdge(out, context.theme.focusEdge, data.baseSize * 1.4, 2);
  if (state === "filteredCommunity") return showEdge(out, data.baseColor, data.baseSize, 1);
  return showEdge(out, data.baseColor, data.baseSize, data.withinCommunity ? 1 : 0);
}

function hideEdge(out: EdgeReducerOutput): EdgeReducerOutput {
  out.hidden = true;
  return out;
}

function showEdge(
  out: EdgeReducerOutput,
  color: string,
  size: number,
  zIndex?: number,
): EdgeReducerOutput {
  out.color = color;
  out.size = size;
  out.hidden = false;
  if (zIndex !== undefined) out.zIndex = zIndex;
  return out;
}

function isDimEdge(endpoints: EdgeEndpoints, context: HighlightContext): boolean {
  if (
    context.hasFilter
    && (!context.state.filteredCommunities.has(endpoints.source.communityId)
      || !context.state.filteredCommunities.has(endpoints.target.communityId))
  ) {
    return true;
  }
  if (context.hasSearch && !(matchesSearch(endpoints.source, context) || matchesSearch(endpoints.target, context))) {
    return true;
  }
  return context.focusSet !== null
    && !(context.focusSet.has(endpoints.sourceId) && context.focusSet.has(endpoints.targetId));
}

function updateHullOpacity(context: HighlightContext): void {
  context.hulls.querySelectorAll("path").forEach((path) => {
    const communityId = Number(path.getAttribute("data-com"));
    const highlight = isHighlightedCommunity(context, communityId);
    path.setAttribute("fill-opacity", highlight ? context.theme.hullFillOpacity : context.theme.hullDimFillOpacity);
    path.setAttribute("stroke-opacity", highlight ? context.theme.hullStrokeOpacity : context.theme.hullDimStrokeOpacity);
  });
}

function isHighlightedCommunity(context: HighlightContext, communityId: number): boolean {
  if (context.hasFilter) return context.state.filteredCommunities.has(communityId);
  if (context.focusSet === null) return true;

  for (const node of context.focusSet) {
    if (context.graph.getNodeAttribute(node, "communityId") === communityId) return true;
  }
  return false;
}
