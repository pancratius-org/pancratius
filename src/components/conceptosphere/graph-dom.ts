export const GRAPH_DOM_IDS = {
  stage: "cs-graph",
  hulls: "cs-hulls",
  panel: "cs-panel",
  panelBody: "cs-panel-body",
  panelClose: "cs-panel-close",
  desktopSearch: "cs-search-desktop",
  legendList: "cs-legend-list",
  legendClear: "cs-legend-clear",
  legendTitle: "cs-legend-title",
  mobileRoot: "cs-mobile",
  mobileSearch: "cs-search-mobile",
  pageHeading: "cs-page-h1",
  pageLede: "cs-page-lede",
  pageMethod: "cs-page-meth",
  counts: "cs-counts",
} as const;

export const GRAPH_DOM_CLASSES = {
  legendItem: "cs-legend-item",
  legendItemMuted: "is-muted",
  legendItemSelected: "is-selected",
  swatch: "cs-sw",
  size: "cs-sz",
} as const;

export const GRAPH_DOM_SELECTORS = {
  desktopModeButtons: ".cs-modebar [data-cs-mode-toggle] button[data-mode]",
  modeToggleRoot: ".cs-modebar",
  mobileTools: ".cs-mobile-tools",
  modeButtons: "[data-cs-mode-toggle] button[data-mode]",
  mobileClusterChip: "[data-cs-cluster-chip]",
  mobileModeSection: "[data-cs-mobile-mode]",
  mobileGroup: "[data-cs-group]",
  mobileRow: "[data-cs-row]",
  mobileEmpty: "[data-cs-empty]",
} as const;
