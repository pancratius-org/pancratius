// Canonical routed-kind ↔ URL-segment mapping.
//
// The site has three routed content kinds (book, poem, project), each rendered
// under a structural-noun URL segment (books, poetry, projects). That mapping was once
// duplicated across ~10 files; this module is now its single TS source of truth.
//
// This file is PURE TypeScript on purpose — it imports nothing from
// `astro:content`, so `astro.config.ts` (which runs outside the content layer)
// can import it too. The matching Python source is `pancratius/kinds.py`; the
// `audit/python/kind_segments.py` audit asserts the two stay in agreement.

/** The routable content kinds, in canonical order. */
export const ROUTED_KINDS = ["book", "poem", "project"] as const;

/** A single routable content kind. */
export type RoutedKind = typeof ROUTED_KINDS[number];

/** The corpus work kinds; projects are routed content, not works. */
export const CORPUS_WORK_KINDS = ["book", "poem"] as const;

/** A corpus work kind: convertible, downloadable, and pairable by language. */
export type CorpusWorkKind = typeof CORPUS_WORK_KINDS[number];

/** The URL segments a routed kind can map to. */
export type RoutedSegment = "books" | "poetry" | "projects";

/** Routed kind → URL segment. */
export const SEGMENT_OF: Record<RoutedKind, RoutedSegment> = {
  book:    "books",
  poem:    "poetry",
  project: "projects",
};

/** URL segment → routed kind (inverse of `SEGMENT_OF`). */
export const KIND_OF_SEGMENT: Record<string, RoutedKind> = {
  books:    "book",
  poetry:   "poem",
  projects: "project",
};
