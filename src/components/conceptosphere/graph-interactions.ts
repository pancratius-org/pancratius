import type { GraphCommunity } from "./graph-data.ts";
import { GRAPH_DOM_CLASSES } from "./graph-dom.ts";
import type { GraphSession } from "./graph-session.ts";
import { hidePanel, showPanel, type PanelHost } from "./panel-render.ts";

export interface GraphInteractionHost extends PanelHost {
  desktopSearch: HTMLInputElement;
  legendList: HTMLElement;
  legendClear: HTMLButtonElement;
}

export interface GraphInteractionBindings {
  dispose: () => void;
}

export function bindGraphInteractions(
  host: GraphInteractionHost,
  session: GraphSession,
): GraphInteractionBindings {
  const disposers: (() => void)[] = [];
  const { renderer, interactions } = session;

  renderer.on("enterNode", ({ node }) => {
    interactions.hover(node);
    session.refresh();
    if (!interactions.pinned) showSessionPanel(host, session, node, false);
  });
  renderer.on("leaveNode", () => {
    interactions.hover(null);
    session.refresh();
    if (!interactions.pinned) hidePanel(host);
  });
  renderer.on("clickNode", ({ node }) => {
    interactions.pin(node);
    session.refresh();
    showSessionPanel(host, session, node, true);
  });
  renderer.on("clickStage", () => {
    if (!interactions.pinned) return;
    interactions.clearFocus();
    session.refresh();
    hidePanel(host);
  });

  const onSearchInput = () => {
    interactions.setSearch(host.desktopSearch.value);
    session.refresh();
  };
  host.desktopSearch.addEventListener("input", onSearchInput);
  disposers.push(() => host.desktopSearch.removeEventListener("input", onSearchInput));

  renderLegend(host, session, disposers);

  return {
    dispose: () => {
      for (const dispose of disposers.splice(0)) dispose();
    },
  };
}

export function clearGraphInteractionState(host: GraphInteractionHost, session: GraphSession): void {
  host.desktopSearch.value = "";
  session.interactions.clearAll();
  updateLegendUI(host, session);
  session.refresh();
  hidePanel(host);
}

export function refreshOpenPanel(host: GraphInteractionHost, session: GraphSession): void {
  if (session.interactions.pinned) {
    showSessionPanel(host, session, session.interactions.pinned, true);
  } else if (session.interactions.hovered) {
    showSessionPanel(host, session, session.interactions.hovered, false);
  }
}

function renderLegend(
  host: GraphInteractionHost,
  session: GraphSession,
  disposers: (() => void)[],
): void {
  host.legendList.replaceChildren();
  for (const community of session.communities.sortedBySize) {
    const button = createLegendButton(community);
    const toggle = () => {
      session.interactions.toggleCommunity(community.id);
      updateLegendUI(host, session);
      session.refresh();
    };
    button.addEventListener("click", toggle);
    disposers.push(() => button.removeEventListener("click", toggle));
    host.legendList.appendChild(button);
  }

  const clearLegend = () => {
    session.interactions.clearCommunityFilter();
    updateLegendUI(host, session);
    session.refresh();
  };
  host.legendClear.addEventListener("click", clearLegend);
  disposers.push(() => host.legendClear.removeEventListener("click", clearLegend));
  updateLegendUI(host, session);
}

function createLegendButton(community: GraphCommunity): HTMLButtonElement {
  const button = document.createElement("button");
  button.type = "button";
  button.className = GRAPH_DOM_CLASSES.legendItem;
  button.dataset.com = String(community.id);
  button.title = community.label;
  button.setAttribute("aria-pressed", "false");

  const swatch = document.createElement("span");
  swatch.className = GRAPH_DOM_CLASSES.swatch;
  swatch.style.background = community.color;

  const label = document.createElement("span");
  label.textContent = community.label;

  const size = document.createElement("span");
  size.className = GRAPH_DOM_CLASSES.size;
  size.textContent = String(community.size);

  button.append(swatch, label, size);
  return button;
}

function updateLegendUI(host: GraphInteractionHost, session: GraphSession): void {
  host.legendList.querySelectorAll<HTMLButtonElement>(`.${GRAPH_DOM_CLASSES.legendItem}`).forEach((item) => {
    const rawCommunity = item.dataset.com;
    if (rawCommunity === undefined) return;
    const communityId = Number(rawCommunity);
    const selected = session.interactions.hasCommunity(communityId);
    item.classList.toggle(GRAPH_DOM_CLASSES.legendItemMuted, session.interactions.hasCommunityFilter && !selected);
    item.classList.toggle(GRAPH_DOM_CLASSES.legendItemSelected, selected);
    item.setAttribute("aria-pressed", selected ? "true" : "false");
  });
  host.legendClear.hidden = !session.interactions.hasCommunityFilter;
}

function showSessionPanel(
  host: GraphInteractionHost,
  session: GraphSession,
  nodeId: string,
  pinned: boolean,
): void {
  showPanel(host, session, nodeId, pinned);
}
