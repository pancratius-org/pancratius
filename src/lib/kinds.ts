// Canonical work-kind ↔ URL-segment mapping.
//
// The site has three kinds of work (book, poem, project), each rendered under a
// structural-noun URL segment (books, poetry, projects). That mapping was once
// duplicated across ~10 files; this module is now its single TS source of truth.
//
// This file is PURE TypeScript on purpose — it imports nothing from
// `astro:content`, so `astro.config.ts` (which runs outside the content layer)
// can import it too. The matching Python source is `scripts/lib/kinds.py`; the
// `scripts/audit/python/kind_segments.py` audit asserts the two stay in agreement.

/** The three work kinds, in canonical order. */
export const WORK_KINDS = ["book", "poem", "project"] as const;

/** A single work kind. */
export type WorkKind = typeof WORK_KINDS[number];

/** The URL segments a work kind can map to. */
export type WorkSegment = "books" | "poetry" | "projects";

/** Work kind → URL segment. */
export const SEGMENT_OF: Record<WorkKind, WorkSegment> = {
  book:    "books",
  poem:    "poetry",
  project: "projects",
};

/** URL segment → work kind (inverse of `SEGMENT_OF`). */
export const KIND_OF_SEGMENT: Record<string, WorkKind> = {
  books:    "book",
  poetry:   "poem",
  projects: "project",
};
