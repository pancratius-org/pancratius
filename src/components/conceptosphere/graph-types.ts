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
  shared_concepts?: TopConceptRef[];
}

export type BookSimilarRef = SimilarRef & { kind: "book" };

export interface TopConceptRef {
  concept_id?: string;
  label?: string;
  lemma?: string;
  count?: number;
  score?: number;
  coverage?: number;
  weight?: number;
  untranslated?: boolean;
}

export interface TopBookRef {
  slug: string;
  kind?: "book";
  title: string;
  count?: number;
}
