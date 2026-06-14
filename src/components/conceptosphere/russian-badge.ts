// The conceptosphere client-runtime twin of `RussianOriginalBadge.astro`.
//
// The graph panels are built in the DOM (not Astro), so the shared RU-only
// degradation treatment is emitted here as elements. It renders the identical
// pill + "Open in Russian" link and reuses the same `.ru-original*` classes the
// Astro component defines, so the two surfaces share one visual treatment.
//
// The `localized` decision is NOT made here — callers pass the resolved RU
// `href` (the book's Russian page) only when the book has fallen back.

import type { ConceptosphereStrings } from "./strings.ts";

export function russianOriginalBadge(
  strings: ConceptosphereStrings,
  href: string,
): HTMLElement {
  const wrap = document.createElement("span");
  wrap.className = "ru-original";

  const pill = document.createElement("span");
  pill.className = "ru-original__pill";
  pill.textContent = strings.russianOriginalBadge;

  const open = document.createElement("a");
  open.className = "ru-original__open";
  open.href = href;
  open.textContent = strings.openInRussianLabel;

  wrap.append(pill, open);
  return wrap;
}
