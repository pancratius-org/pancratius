// The single predicate for "which Markdown headings become a table of contents,
// and therefore whether a page renders a ToC rail at all". `<TableOfContents>`
// and every page that gates a two-column ToC layout on `useToc` share this so
// they cannot disagree: a page that counts a heading the ToC then hides renders
// the rail track with nothing in it, collapsing the body into the rail width.

import type { MarkdownHeading } from "astro";

/** Default ToC depth window: section (h2) and subsection (h3) only. */
export const TOC_MIN_DEPTH = 2;
export const TOC_MAX_DEPTH = 3;

// The screen-reader-only `<h2 id="footnote-label">` the GFM footnote renderer
// injects to label the footnotes section. It is a heading in the parsed
// `headings` array but never a navigable section, so it is not a ToC entry.
const FOOTNOTE_LABEL_SLUG = "footnote-label";

/** The headings that become visible ToC entries. */
export function tocHeadings(
  headings: readonly MarkdownHeading[],
  minDepth: number = TOC_MIN_DEPTH,
  maxDepth: number = TOC_MAX_DEPTH,
): MarkdownHeading[] {
  return headings.filter(
    h => h.depth >= minDepth && h.depth <= maxDepth && h.slug !== FOOTNOTE_LABEL_SLUG,
  );
}

/** Whether a page has any ToC-worthy heading — gates the two-column rail layout. */
export function hasToc(
  headings: readonly MarkdownHeading[],
  minDepth: number = TOC_MIN_DEPTH,
  maxDepth: number = TOC_MAX_DEPTH,
): boolean {
  return tocHeadings(headings, minDepth, maxDepth).length > 0;
}
