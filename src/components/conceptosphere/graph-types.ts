export type ConceptosphereMode = "concepts" | "books";

export const CONCEPTOSPHERE_MODES = ["concepts", "books"] as const;

export function isConceptosphereMode(value: unknown): value is ConceptosphereMode {
  return value === "concepts" || value === "books";
}

export interface SimilarRef {
  slug: string;
  kind: "book" | "poem" | "project";
  title: string;
  weight?: number;
}

export type BookSimilarRef = SimilarRef & { kind: "book" };

export interface TopConceptRef {
  label?: string;
  lemma?: string;
  count?: number;
}

export interface TopBookRef {
  slug: string;
  kind?: "book";
  title: string;
  count?: number;
}
