import type { ConceptosphereStrings } from "./strings";

export type ConceptosphereMode = "concepts" | "books";

export interface PageConfig {
  conceptsUrl: string;
  booksUrl: string;
  initialMode: ConceptosphereMode;
  locale: "ru" | "en";
  modeCounts: Record<ConceptosphereMode, string>;
  /**
   * Per-book lookup keyed by the RU slug (the graph payload's identity).
   * `href` is resolved at build time, so runtime code never has to invent
   * locale-specific book URLs.
   */
  bookSlugInfo: Record<string, { number: number; title: string; href: string }>;
  coverUrls: Record<string, string>;
  strings: ConceptosphereStrings;
}

export interface Community {
  id: number;
  label: string;
  size: number;
}

export interface SimilarRef {
  slug: string;
  kind: "book" | "poem" | "project";
  title: string;
  weight?: number;
}

export type BookSimilarRef = SimilarRef & { kind: "book" };

export interface NodeJson {
  id: string;
  label?: string;
  lemma?: string;
  title?: string;
  slug?: string;
  number?: number;
  tags?: string[];
  community: number;
  frequency?: number;
  centrality?: number;
  degree?: number;
  top_concepts?: { label?: string; lemma?: string; count?: number }[];
  top_books?: { slug: string; kind?: "book"; title: string; count?: number }[];
  top_similar?: SimilarRef[];
  top_similar_embed?: SimilarRef[];
}

export interface EdgeJson {
  source: string;
  target: string;
  weight: number;
  npmi?: number;
}

export interface GraphPayload {
  stats: Record<string, number | string | undefined>;
  communities: Community[];
  nodes: NodeJson[];
  edges: EdgeJson[];
}
