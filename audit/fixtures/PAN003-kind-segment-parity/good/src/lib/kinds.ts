// Fixture (PAN003-kind-segment-parity / good): TS kind→segment SoT that AGREES
// with the sibling pancratius/kinds.py — the cross-language audit must stay
// silent.
type WorkKind = "book" | "poem" | "project";
type WorkSegment = "books" | "poetry" | "projects";
export const SEGMENT_OF: Record<WorkKind, WorkSegment> = {
  book: "books",
  poem: "poetry",
  project: "projects",
};
