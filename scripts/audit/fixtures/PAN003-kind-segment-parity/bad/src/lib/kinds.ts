// Fixture (PAN003-kind-segment-parity / bad): TS kind→segment SoT that DISAGREES
// with the sibling scripts/lib/kinds.py (poem → "poems" here, but Python maps
// poem → "poetry") — the cross-language audit must fire.
type WorkKind = "book" | "poem" | "project";
type WorkSegment = "books" | "poems" | "projects";
export const SEGMENT_OF: Record<WorkKind, WorkSegment> = {
  book: "books",
  poem: "poems",
  project: "projects",
};
