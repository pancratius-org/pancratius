// Build-time data assembly for the static conceptosphere routes.
//
// The public graph payloads are RU-derived. This helper resolves the
// localized book metadata that the client graph needs so the two route files
// cannot drift in slug collection, cover lookup, or fallback behavior.

import { coverAssetUrl } from "@/lib/covers";
import { loadBooksGraph, loadConceptsGraph } from "@/lib/conceptosphere";
import { DEFAULT_LOCALE, kindIndexUrl, workUrl, type Locale } from "@/lib/i18n";
import { findPair } from "@/lib/works";

import { communityColor } from "./palette";
import type { ConceptosphereStrings } from "./strings";
import type { ConceptosphereMode } from "./runtime-types";

type BookSlugInfo = Record<string, { number: number; title: string; href: string }>;

type ConceptosphereCommunityView = {
  id: number;
  label: string;
  color: string;
};

export type ConceptosphereConceptRow = {
  id: string;
  label: string;
  community: number;
  frequency: number;
  topBooks: { slug: string; title: string; href: string }[];
  searchHay: string;
};

export type ConceptosphereBookRow = {
  slug: string;
  number: number;
  title: string;
  community: number;
  tags: readonly string[];
  href: string;
  coverUrl: string | null;
  topConcepts: { label: string }[];
  searchHay: string;
};

export type ConceptosphereSection<Row> = {
  com: ConceptosphereCommunityView;
  rows: Row[];
};

export interface ConceptospherePageData {
  modeCounts: Record<ConceptosphereMode, string>;
  bookSlugInfo: BookSlugInfo;
  coverUrls: Record<string, string>;
  booksIndexHref: string;
  conceptSections: ConceptosphereSection<ConceptosphereConceptRow>[];
  bookSections: ConceptosphereSection<ConceptosphereBookRow>[];
}

export async function loadConceptospherePageData(
  locale: Locale,
  strings: ConceptosphereStrings,
): Promise<ConceptospherePageData> {
  const booksGraph = loadBooksGraph();
  const conceptsGraph = loadConceptsGraph();

  const referencedBookSlugs = new Set<string>();
  for (const n of booksGraph.nodes) {
    referencedBookSlugs.add(n.slug);
    for (const s of n.top_similar ?? []) {
      if (s.kind === "book") referencedBookSlugs.add(s.slug);
    }
    for (const s of n.top_similar_embed ?? []) {
      if (s.kind === "book") referencedBookSlugs.add(s.slug);
    }
  }
  for (const n of conceptsGraph.nodes) {
    for (const b of n.top_books ?? []) {
      referencedBookSlugs.add(b.slug);
    }
  }

  const coverUrls: Record<string, string> = {};
  const bookSlugInfo: BookSlugInfo = {};
  const bookBySlug = new Map(booksGraph.nodes.map((n) => [n.slug, n]));

  for (const slug of referencedBookSlugs) {
    const node = bookBySlug.get(slug);
    if (!node) continue;

    const pair = await findPair("book", node.number);
    // Display-with-fallback: prefer the entry authored in the active locale;
    // otherwise fall back to the default-locale entry and link to its route.
    const authored = pair?.entries[locale] ?? null;
    const display = authored ?? pair?.entries[DEFAULT_LOCALE] ?? null;
    const linkLocale: Locale = authored ? locale : DEFAULT_LOCALE;
    const linkSlug = display?.data.slug ?? node.slug;
    const title = display?.data.title ?? node.title;

    bookSlugInfo[slug] = {
      number: node.number,
      title,
      href: workUrl("book", linkSlug, linkLocale),
    };

    if (!pair) continue;
    const url = coverAssetUrl(pair, locale);
    if (url) coverUrls[`book:${slug}`] = url;
  }

  const bookRows: ConceptosphereBookRow[] = booksGraph.nodes.map((node) => {
    const info = bookSlugInfo[node.slug];
    const title = info?.title ?? node.title;
    const allConcepts = node.top_concepts ?? [];
    const displayedConcepts = allConcepts.slice(0, 8).map((c) => ({ label: c.label }));
    return {
      slug: node.slug,
      number: node.number,
      title,
      community: node.community,
      tags: node.tags ?? [],
      href: info?.href ?? workUrl("book", node.slug, DEFAULT_LOCALE),
      coverUrl: coverUrls[`book:${node.slug}`] ?? null,
      topConcepts: displayedConcepts,
      searchHay: [
        title,
        node.title,
        node.slug,
        String(node.number),
        ...(node.tags ?? []),
        ...allConcepts.flatMap((c) => [c.label, c.lemma]),
      ].join(" ").toLowerCase(),
    };
  });

  const conceptRows: ConceptosphereConceptRow[] = conceptsGraph.nodes.map((node) => {
    const top = (node.top_books ?? []).slice(0, 5);
    const topBooks = top.map((b) => ({
      slug: b.slug,
      title: bookSlugInfo[b.slug]?.title ?? b.title,
      href: bookSlugInfo[b.slug]?.href ?? workUrl("book", b.slug, DEFAULT_LOCALE),
    }));
    const topBookTitles = new Set([...top.map((b) => b.title), ...topBooks.map((b) => b.title)]);
    return {
      id: node.id,
      label: node.label,
      community: node.community,
      frequency: node.frequency,
      topBooks,
      searchHay: [node.label, node.lemma, ...topBookTitles].join(" ").toLowerCase(),
    };
  });

  const modeCounts: Record<ConceptosphereMode, string> = {
    concepts: countLine(
      {
        stats: conceptsGraph.stats,
        nodesLength: conceptsGraph.nodes.length,
        edgesLength: conceptsGraph.edges.length,
        communitiesLength: conceptsGraph.communities.length,
      },
      "concepts",
      strings,
    ),
    books: countLine(
      {
        stats: booksGraph.stats,
        nodesLength: booksGraph.nodes.length,
        edgesLength: booksGraph.edges.length,
        communitiesLength: booksGraph.communities.length,
      },
      "books",
      strings,
    ),
  };

  return {
    modeCounts,
    bookSlugInfo,
    coverUrls,
    booksIndexHref: kindIndexUrl("book", locale),
    conceptSections: groupRows(
      conceptsGraph.communities,
      conceptRows,
      (row) => row.community,
      (a, b) => b.frequency - a.frequency,
    ),
    bookSections: groupRows(
      booksGraph.communities,
      bookRows,
      (row) => row.community,
      (a, b) => a.number - b.number,
    ),
  };
}

function countLine(
  graph: {
    stats: Record<string, unknown>;
    nodesLength: number;
    edgesLength: number;
    communitiesLength: number;
  },
  mode: ConceptosphereMode,
  strings: ConceptosphereStrings,
): string {
  const nodes =
    typeof graph.stats.kept_nodes === "number" ? graph.stats.kept_nodes :
    typeof graph.stats.books === "number" ? graph.stats.books :
    graph.nodesLength;
  const edges = typeof graph.stats.edges === "number" ? graph.stats.edges : graph.edgesLength;
  const communities = typeof graph.stats.communities === "number"
    ? graph.stats.communities
    : graph.communitiesLength;
  return `${nodes} ${strings.modes[mode].countsNoun} · ${edges} ${strings.edgesNoun} · ${communities} ${strings.communitiesNoun}`;
}

function groupRows<Row>(
  communities: { id: number; label: string }[],
  rows: Row[],
  communityFor: (row: Row) => number,
  sortRows: (a: Row, b: Row) => number,
): ConceptosphereSection<Row>[] {
  const map = new Map<number, Row[]>();
  for (const row of rows) {
    const community = communityFor(row);
    const arr = map.get(community) ?? [];
    arr.push(row);
    map.set(community, arr);
  }
  return communities
    .filter((c) => map.has(c.id))
    .sort((a, b) => (map.get(b.id)?.length ?? 0) - (map.get(a.id)?.length ?? 0))
    .map((c) => ({
      com: { id: c.id, label: c.label, color: communityColor(c.id) },
      rows: (map.get(c.id) ?? []).sort(sortRows),
    }));
}
