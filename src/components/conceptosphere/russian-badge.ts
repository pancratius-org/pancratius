// The conceptosphere client-runtime twin of `RussianOriginalBadge.astro`.
//
// The graph panels are built in the DOM (not Astro), so the shared RU-only
// degradation treatment is emitted here as elements. It renders the identical
// pill and reuses the same `.ru-original*` classes the Astro component defines,
// so the two surfaces share one visual treatment. The book's own link already
// points to the Russian page, so the pill alone declares the fallback.
//
// The `localized` decision is NOT made here — callers emit the badge only when
// the book has fallen back to its Russian page.

import type { ConceptosphereStrings } from "./strings.ts";

export function russianOriginalBadge(strings: ConceptosphereStrings): HTMLElement {
  const wrap = document.createElement("span");
  wrap.className = "ru-original";

  const pill = document.createElement("span");
  pill.className = "ru-original__pill";
  pill.textContent = strings.russianOriginalBadge;

  wrap.append(pill);
  return wrap;
}
