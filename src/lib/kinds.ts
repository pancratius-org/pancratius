// Routed-kind ↔ URL-segment mapping. Single TS source of truth.
//
// Pure TypeScript (no `astro:content` import) so `astro.config.ts` can import
// it. Python mirror: `pancratius/kinds.py`; cross-language guard:
// `audit/python/kind_segments.py`.

/** The routable content kinds, in canonical order. */
export const ROUTED_KINDS = ["book", "poem", "project", "video", "message"] as const;

/** A single routable content kind. */
export type RoutedKind = typeof ROUTED_KINDS[number];

/** The corpus work kinds. Projects and videos route but aren't works. */
export const CORPUS_WORK_KINDS = ["book", "poem"] as const;

/** A corpus work kind: convertible, downloadable, and pairable by language. */
export type CorpusWorkKind = typeof CORPUS_WORK_KINDS[number];

/** The URL segments a routed kind can map to. */
export type RoutedSegment = "books" | "poetry" | "projects" | "videos" | "messages";

/** Routed kind → URL segment. Every segment is a structural English noun; the
 * displayed section label is localized separately («Послания» / Epistles for
 * messages). */
export const SEGMENT_OF: Record<RoutedKind, RoutedSegment> = {
  book:    "books",
  poem:    "poetry",
  project: "projects",
  video:   "videos",
  message: "messages",
};

/** URL segment → routed kind (inverse of `SEGMENT_OF`). */
export const KIND_OF_SEGMENT: Record<RoutedSegment, RoutedKind> = {
  books:    "book",
  poetry:   "poem",
  projects: "project",
  videos:   "video",
  messages: "message",
};

/** True when an arbitrary path segment is one of the routed content segments. */
export function isRoutedSegment(value: string): value is RoutedSegment {
  return Object.prototype.hasOwnProperty.call(KIND_OF_SEGMENT, value);
}
