import type { ConceptosphereMode, PageConfig } from "./runtime-types";

export interface MobileController {
  setMode: (mode: ConceptosphereMode) => void;
  dispose: () => void;
}

export function wireMobile(
  cfg: PageConfig,
  onModeChange: (mode: ConceptosphereMode) => void,
): MobileController | null {
  const foundRoot = document.getElementById("cs-mobile");
  if (!foundRoot) return null;
  const root = foundRoot;
  if (root.dataset.csWired === "true") return null;
  root.dataset.csWired = "true";

  const allModes: ConceptosphereMode[] = ["concepts", "books"];
  const mobileTools = document.querySelector<HTMLElement>(".cs-mobile-tools");
  const mobileModeButtons = () =>
    (mobileTools ?? document).querySelectorAll<HTMLButtonElement>("[data-cs-mode-toggle] button[data-mode]");

  const mobileSearch = document.getElementById("cs-search-mobile") as HTMLInputElement | null;
  const filterSets: Record<ConceptosphereMode, Set<number>> = { concepts: new Set(), books: new Set() };
  const disposers: (() => void)[] = [
    () => { delete root.dataset.csWired; },
  ];

  applyMobileMode(cfg.initialMode);
  mobileModeButtons().forEach((btn) => {
    const onClick = () => {
      const m = btn.getAttribute("data-mode") as ConceptosphereMode | null;
      if (!m) return;
      onModeChange(m);
    };
    btn.addEventListener("click", onClick);
    disposers.push(() => btn.removeEventListener("click", onClick));
  });

  const onSearchInput = () => {
    const current = currentMobileMode();
    if (current) applyMobileFilters(current);
  };
  mobileSearch?.addEventListener("input", onSearchInput);
  if (mobileSearch) disposers.push(() => mobileSearch.removeEventListener("input", onSearchInput));

  root.querySelectorAll<HTMLButtonElement>("[data-cs-cluster-chip]").forEach((btn) => {
    const onClick = () => {
      const mode = btn.closest<HTMLElement>("[data-cs-mobile-mode]")?.getAttribute("data-cs-mobile-mode") as ConceptosphereMode | null;
      if (!mode) return;
      const rawCom = btn.getAttribute("data-com");
      if (rawCom == null || rawCom === "") {
        filterSets[mode].clear();
      } else {
        const cid = Number(rawCom);
        if (Number.isNaN(cid)) return;
        if (filterSets[mode].has(cid)) filterSets[mode].delete(cid);
        else filterSets[mode].add(cid);
      }
      reflectChipState(mode);
      applyMobileFilters(mode);
    };
    btn.addEventListener("click", onClick);
    disposers.push(() => btn.removeEventListener("click", onClick));
  });

  function applyMobileMode(mode: ConceptosphereMode): void {
    const modeCopy = cfg.strings.modes[mode];
    setModeButtons(mobileTools ?? root, mode);
    for (const m of allModes) {
      const section = root.querySelector<HTMLElement>(`[data-cs-mobile-mode="${m}"]`);
      if (section) section.hidden = m !== mode;
    }
    if (mobileSearch) {
      mobileSearch.placeholder = modeCopy.searchPlaceholder;
      mobileSearch.value = "";
      applyMobileFilters(mode);
    }
    setText("cs-page-h1", modeCopy.h1);
    setText("cs-page-lede", modeCopy.lede);
    setText("cs-page-meth", modeCopy.meth);
    setText("cs-counts", cfg.modeCounts[mode]);
  }

  function currentMobileMode(): ConceptosphereMode | null {
    for (const m of allModes) {
      const section = root.querySelector<HTMLElement>(`[data-cs-mobile-mode="${m}"]`);
      if (section && !section.hidden) return m;
    }
    return null;
  }

  function reflectChipState(mode: ConceptosphereMode): void {
    const chips = root.querySelectorAll<HTMLButtonElement>(`[data-cs-mobile-mode="${mode}"] [data-cs-cluster-chip]`);
    chips.forEach((c) => {
      const ccid = c.getAttribute("data-com");
      const isAll = ccid === "" || ccid == null;
      const set = filterSets[mode];
      const active = isAll ? set.size === 0 : set.has(Number(ccid));
      c.setAttribute("aria-pressed", active ? "true" : "false");
    });
  }

  function applyMobileFilters(mode: ConceptosphereMode): void {
    const q = (mobileSearch?.value ?? "").trim().toLowerCase();
    const filter = filterSets[mode];
    const section = root.querySelector<HTMLElement>(`[data-cs-mobile-mode="${mode}"]`);
    if (!section) return;
    const groups = section.querySelectorAll<HTMLElement>("[data-cs-group]");
    let totalShown = 0;
    groups.forEach((group) => {
      const cid = Number(group.getAttribute("data-com"));
      const groupAllowed = filter.size === 0 || filter.has(cid);
      let groupShown = 0;
      const rows = group.querySelectorAll<HTMLElement>("[data-cs-row]");
      rows.forEach((row) => {
        if (!groupAllowed) {
          row.hidden = true;
          return;
        }
        const hay = (row.getAttribute("data-search") ?? "").toLowerCase();
        const ok = !q || hay.includes(q);
        row.hidden = !ok;
        if (ok) groupShown++;
      });
      group.hidden = !groupAllowed || groupShown === 0;
      totalShown += groupAllowed ? groupShown : 0;
    });
    const empty = section.querySelector<HTMLElement>("[data-cs-empty]");
    if (empty) empty.hidden = totalShown > 0;
  }

  return {
    setMode: applyMobileMode,
    dispose: () => {
      for (const dispose of disposers.splice(0)) dispose();
    },
  };
}

function setModeButtons(root: ParentNode, mode: ConceptosphereMode): void {
  root.querySelectorAll<HTMLButtonElement>("[data-cs-mode-toggle] button[data-mode]").forEach((button) => {
    const isActive = button.getAttribute("data-mode") === mode;
    button.setAttribute("aria-pressed", isActive ? "true" : "false");
  });
}

function setText(id: string, text: string): void {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}
