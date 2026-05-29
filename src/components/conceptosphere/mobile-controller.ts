import { GRAPH_DOM_IDS, GRAPH_DOM_SELECTORS } from "./graph-dom.ts";
import { CONCEPTOSPHERE_MODES, isConceptosphereMode, type ConceptosphereMode } from "./graph-types.ts";
import type { PageConfig } from "./page-config.ts";

export interface MobileController {
  setMode: (mode: ConceptosphereMode) => void;
  dispose: () => void;
}

export function wireMobile(
  cfg: PageConfig,
  onModeChange: (mode: ConceptosphereMode) => void,
): MobileController | null {
  const root = claimMobileRoot();
  if (!root) return null;

  return new MobileListController(cfg, onModeChange, {
    root,
    tools: document.querySelector<HTMLElement>(GRAPH_DOM_SELECTORS.mobileTools),
    search: mobileSearchInput(),
  });
}

interface MobileListHost {
  root: HTMLElement;
  tools: HTMLElement | null;
  search: HTMLInputElement | null;
}

class MobileListController implements MobileController {
  private readonly filterSets: Record<ConceptosphereMode, Set<number>> = {
    concepts: new Set(),
    books: new Set(),
  };
  private readonly disposers: (() => void)[] = [
    () => { delete this.host.root.dataset.csWired; },
  ];

  constructor(
    private readonly cfg: PageConfig,
    private readonly onModeChange: (mode: ConceptosphereMode) => void,
    private readonly host: MobileListHost,
  ) {
    this.setMode(cfg.initialMode);
    this.bindModeButtons();
    this.bindSearch();
    this.bindClusterChips();
  }

  setMode(mode: ConceptosphereMode): void {
    const modeCopy = this.cfg.strings.modes[mode];
    setModeButtons(this.host.tools ?? this.host.root, mode);
    this.showModeSection(mode);
    this.resetSearch(mode, modeCopy.searchPlaceholder);
    setModeCopy(this.cfg, mode);
  }

  dispose(): void {
    for (const dispose of this.disposers.splice(0)) dispose();
  }

  private modeButtons(): NodeListOf<HTMLButtonElement> {
    return (this.host.tools ?? document).querySelectorAll<HTMLButtonElement>(GRAPH_DOM_SELECTORS.modeButtons);
  }

  private bindModeButtons(): void {
    this.modeButtons().forEach((button) => {
      const onClick = () => {
        const mode = modeFromAttribute(button.getAttribute("data-mode"));
        if (mode) this.onModeChange(mode);
      };
      button.addEventListener("click", onClick);
      this.disposers.push(() => button.removeEventListener("click", onClick));
    });
  }

  private bindSearch(): void {
    const search = this.host.search;
    if (!search) return;
    const onSearchInput = () => {
      const current = this.currentMode();
      if (current) this.applyFilters(current);
    };
    search.addEventListener("input", onSearchInput);
    this.disposers.push(() => search.removeEventListener("input", onSearchInput));
  }

  private bindClusterChips(): void {
    this.host.root.querySelectorAll<HTMLButtonElement>(GRAPH_DOM_SELECTORS.mobileClusterChip).forEach((button) => {
      const onClick = () => this.toggleChip(button);
      button.addEventListener("click", onClick);
      this.disposers.push(() => button.removeEventListener("click", onClick));
    });
  }

  private toggleChip(button: HTMLButtonElement): void {
    const mode = chipMode(button);
    if (!mode) return;
    const clusterId = chipClusterId(button);
    if (clusterId === null) return;

    const filters = this.filterSets[mode];
    if (clusterId === "all") filters.clear();
    else toggleSetValue(filters, clusterId);

    this.reflectChipState(mode);
    this.applyFilters(mode);
  }

  private showModeSection(mode: ConceptosphereMode): void {
    for (const item of CONCEPTOSPHERE_MODES) {
      const section = mobileModeSection(this.host.root, item);
      if (section) section.hidden = item !== mode;
    }
  }

  private resetSearch(mode: ConceptosphereMode, placeholder: string): void {
    if (!this.host.search) return;
    this.host.search.placeholder = placeholder;
    this.host.search.value = "";
    this.applyFilters(mode);
  }

  private currentMode(): ConceptosphereMode | null {
    for (const mode of CONCEPTOSPHERE_MODES) {
      const section = mobileModeSection(this.host.root, mode);
      if (section && !section.hidden) return mode;
    }
    return null;
  }

  private reflectChipState(mode: ConceptosphereMode): void {
    const chips = this.host.root.querySelectorAll<HTMLButtonElement>(
      `${mobileModeSectionSelector(mode)} ${GRAPH_DOM_SELECTORS.mobileClusterChip}`,
    );
    chips.forEach((chip) => {
      const clusterId = chipClusterId(chip);
      const active = clusterId === "all"
        ? this.filterSets[mode].size === 0
        : clusterId !== null && this.filterSets[mode].has(clusterId);
      chip.setAttribute("aria-pressed", active ? "true" : "false");
    });
  }

  private applyFilters(mode: ConceptosphereMode): void {
    const query = (this.host.search?.value ?? "").trim().toLowerCase();
    const section = mobileModeSection(this.host.root, mode);
    if (!section) return;

    let totalShown = 0;
    section.querySelectorAll<HTMLElement>(GRAPH_DOM_SELECTORS.mobileGroup).forEach((group) => {
      totalShown += filterMobileGroup(group, this.filterSets[mode], query);
    });
    const empty = section.querySelector<HTMLElement>(GRAPH_DOM_SELECTORS.mobileEmpty);
    if (empty) empty.hidden = totalShown > 0;
  }
}

function claimMobileRoot(): HTMLElement | null {
  const root = document.getElementById(GRAPH_DOM_IDS.mobileRoot);
  if (!root) return null;
  if (root.dataset.csWired === "true") return null;
  root.dataset.csWired = "true";
  return root;
}

function mobileSearchInput(): HTMLInputElement | null {
  const el = document.getElementById(GRAPH_DOM_IDS.mobileSearch);
  return el instanceof HTMLInputElement ? el : null;
}

function chipMode(button: HTMLButtonElement): ConceptosphereMode | null {
  return modeFromAttribute(
    button.closest<HTMLElement>(GRAPH_DOM_SELECTORS.mobileModeSection)?.getAttribute("data-cs-mobile-mode"),
  );
}

function chipClusterId(button: HTMLButtonElement): number | "all" | null {
  const raw = button.getAttribute("data-com");
  if (raw == null || raw === "") return "all";
  const clusterId = Number(raw);
  return Number.isNaN(clusterId) ? null : clusterId;
}

function toggleSetValue<T>(set: Set<T>, value: T): void {
  if (set.has(value)) set.delete(value);
  else set.add(value);
}

function filterMobileGroup(group: HTMLElement, filters: ReadonlySet<number>, query: string): number {
  const clusterId = Number(group.getAttribute("data-com"));
  const groupAllowed = filters.size === 0 || filters.has(clusterId);
  const visibleRows = groupAllowed ? filterMobileRows(group, query) : hideMobileRows(group);
  group.hidden = !groupAllowed || visibleRows === 0;
  return groupAllowed ? visibleRows : 0;
}

function filterMobileRows(group: HTMLElement, query: string): number {
  let visibleRows = 0;
  group.querySelectorAll<HTMLElement>(GRAPH_DOM_SELECTORS.mobileRow).forEach((row) => {
    const visible = rowMatchesQuery(row, query);
    row.hidden = !visible;
    if (visible) visibleRows++;
  });
  return visibleRows;
}

function hideMobileRows(group: HTMLElement): number {
  group.querySelectorAll<HTMLElement>(GRAPH_DOM_SELECTORS.mobileRow).forEach((row) => {
    row.hidden = true;
  });
  return 0;
}

function rowMatchesQuery(row: HTMLElement, query: string): boolean {
  return !query || (row.getAttribute("data-search") ?? "").toLowerCase().includes(query);
}

function setModeButtons(root: ParentNode, mode: ConceptosphereMode): void {
  root.querySelectorAll<HTMLButtonElement>(GRAPH_DOM_SELECTORS.modeButtons).forEach((button) => {
    const isActive = modeFromAttribute(button.getAttribute("data-mode")) === mode;
    button.setAttribute("aria-pressed", isActive ? "true" : "false");
  });
}

function modeFromAttribute(value: string | null | undefined): ConceptosphereMode | null {
  return isConceptosphereMode(value) ? value : null;
}

function mobileModeSection(root: ParentNode, mode: ConceptosphereMode): HTMLElement | null {
  return root.querySelector<HTMLElement>(mobileModeSectionSelector(mode));
}

function mobileModeSectionSelector(mode: ConceptosphereMode): string {
  return `[data-cs-mobile-mode="${mode}"]`;
}

function setModeCopy(cfg: PageConfig, mode: ConceptosphereMode): void {
  const modeCopy = cfg.strings.modes[mode];
  setText(GRAPH_DOM_IDS.pageHeading, modeCopy.h1);
  setText(GRAPH_DOM_IDS.pageLede, modeCopy.lede);
  setText(GRAPH_DOM_IDS.pageMethod, modeCopy.meth);
  setText(GRAPH_DOM_IDS.counts, cfg.modeCounts[mode]);
}

function setText(id: string, text: string): void {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}
