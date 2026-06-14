// Build-time data assembly for the static conceptosphere routes.
//
// The public graph payloads are RU-derived. This helper resolves the
// localized book metadata that the client graph needs so the two route files
// cannot drift in slug collection, cover lookup, or fallback behavior.

import { coverAssetUrl } from "@/lib/covers";
import { loadBooksGraph, loadConceptsGraph, type BooksGraph, type ConceptsGraph } from "@/lib/conceptosphere";
import { kindIndexUrl, workUrl, type Locale } from "@/lib/i18n";
import { displayWorkEntry, findPair, workTags } from "@/lib/works";

import { communityColor } from "./palette.ts";
import type { ConceptosphereStrings } from "./strings.ts";
import type { ConceptosphereMode } from "./graph-types.ts";

type BookSlugInfo = Record<string, { number: number; title: string; href: string; localized: boolean; tags: readonly string[] }>;
type BooksGraphNode = BooksGraph["nodes"][number];
type ConceptsGraphNode = ConceptsGraph["nodes"][number];

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
  // `localized` carries the same RU-only fallback decision book rows use, so a
  // concept's top-book list can badge a Russian original on /en/. For a fallback
  // book `href` already resolves to its Russian page (the badge's open link).
  topBooks: { slug: string; title: string; href: string; localized: boolean }[];
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
  localized: boolean;
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

interface ConceptosphereGraphs {
  books: BooksGraph;
  concepts: ConceptsGraph;
}

interface LocalizedBookCatalogEntry {
  number: number;
  title: string;
  href: string;
  coverUrl: string | null;
  /** True when the link resolves to the requested locale; false for a RU-only book served on /en/. */
  localized: boolean;
  /** Tags from the resolved-locale frontmatter: EN for an EN-paired book, RU for a RU-only one. */
  tags: readonly string[];
}

type LocalizedBookCatalog = ReadonlyMap<string, LocalizedBookCatalogEntry>;

export async function loadConceptospherePageData(
  locale: Locale,
  strings: ConceptosphereStrings,
): Promise<ConceptospherePageData> {
  const graphs = loadConceptosphereGraphs(locale);
  const bookCatalog = await resolveLocalizedBookCatalog(graphs, locale);
  const bookRows = graphs.books.nodes.map((node) => bookRow(node, bookCatalog));
  const conceptRows = graphs.concepts.nodes.map((node) => conceptRow(node, bookCatalog));

  return {
    modeCounts: modeCounts(graphs, strings),
    bookSlugInfo: bookSlugInfoRecord(bookCatalog),
    coverUrls: coverUrlRecord(bookCatalog),
    booksIndexHref: kindIndexUrl("book", locale),
    conceptSections: groupRows(
      graphs.concepts.communities,
      conceptRows,
      (row) => row.community,
      (a, b) => b.frequency - a.frequency,
    ),
    bookSections: groupRows(
      graphs.books.communities,
      bookRows,
      (row) => row.community,
      (a, b) => a.number - b.number,
    ),
  };
}

// The mobile list is server-rendered from these graphs, so it MUST read the
// same per-locale payload the desktop graph fetches (the build-time RU⋈overlay
// join) — not the RU source — or /en/ mobile would show Russian concept and
// community labels under an English URL.
function loadConceptosphereGraphs(locale: Locale): ConceptosphereGraphs {
  return {
    books: loadBooksGraph(locale),
    concepts: loadConceptsGraph(locale),
  };
}

async function resolveLocalizedBookCatalog(
  graphs: ConceptosphereGraphs,
  locale: Locale,
): Promise<LocalizedBookCatalog> {
  const bookBySlug = new Map(graphs.books.nodes.map((node) => [node.slug, node]));
  const catalog = new Map<string, LocalizedBookCatalogEntry>();

  for (const slug of referencedBookSlugs(graphs)) {
    catalog.set(slug, await resolveLocalizedBook(slug, bookBySlug, locale));
  }
  return catalog;
}

function referencedBookSlugs(graphs: ConceptosphereGraphs): Set<string> {
  const slugs = new Set<string>();
  for (const node of graphs.books.nodes) addBookNodeReferences(slugs, node);
  for (const node of graphs.concepts.nodes) addConceptNodeReferences(slugs, node);
  return slugs;
}

function addBookNodeReferences(slugs: Set<string>, node: BooksGraphNode): void {
  slugs.add(node.slug);
  for (const ref of [...node.top_similar, ...(node.top_similar_embed ?? [])]) {
    if (ref.kind === "book") slugs.add(ref.slug);
  }
}

function addConceptNodeReferences(slugs: Set<string>, node: ConceptsGraphNode): void {
  for (const book of node.top_books) slugs.add(book.slug);
}

async function resolveLocalizedBook(
  slug: string,
  bookBySlug: ReadonlyMap<string, BooksGraphNode>,
  locale: Locale,
): Promise<LocalizedBookCatalogEntry> {
  const node = bookBySlug.get(slug);
  if (!node) throw new Error(`conceptosphere graph references unknown book slug ${JSON.stringify(slug)}`);

  const pair = await findPair("book", node.number);
  if (!pair) throw new Error(`conceptosphere graph book #${node.number} (${slug}) has no matching content pair`);

  const { entry, linkLocale } = displayWorkEntry(pair, locale);
  return {
    number: node.number,
    title: entry.data.title,
    href: workUrl("book", entry.data.slug, linkLocale),
    coverUrl: coverAssetUrl(pair, locale),
    // The link is "localized" only when it resolves to the requested locale.
    // A RU-only book on /en/ falls back to RU (linkLocale "ru" ≠ "en") → badge.
    localized: linkLocale === locale,
    // Tags come from the resolved-locale entry: an EN-paired book yields its EN
    // frontmatter tags; a RU-only book stays RU (no EN frontmatter exists). This
    // is why the graph node's own RU `tags` are NOT the source on /en/.
    tags: workTags(entry),
  };
}

function bookSlugInfoRecord(catalog: LocalizedBookCatalog): BookSlugInfo {
  return Object.fromEntries(
    [...catalog].map(([slug, info]) => [
      slug,
      { number: info.number, title: info.title, href: info.href, localized: info.localized, tags: info.tags },
    ]),
  );
}

function coverUrlRecord(catalog: LocalizedBookCatalog): Record<string, string> {
  const coverUrls: Record<string, string> = {};
  for (const [slug, info] of catalog) {
    if (info.coverUrl) coverUrls[`book:${slug}`] = info.coverUrl;
  }
  return coverUrls;
}

function bookRow(node: BooksGraphNode, catalog: LocalizedBookCatalog): ConceptosphereBookRow {
  const info = requireBookInfo(catalog, node.slug);
  const allConcepts = node.top_concepts;
  return {
    slug: node.slug,
    number: node.number,
    title: info.title,
    community: node.community,
    // Resolved-locale frontmatter tags (EN for an EN-paired book), not the
    // graph node's RU `tags`, so /en/ never leaks Russian tags under an EN URL.
    tags: info.tags,
    href: info.href,
    coverUrl: info.coverUrl,
    localized: info.localized,
    topConcepts: allConcepts.slice(0, 8).map((concept) => ({ label: concept.label })),
    searchHay: bookSearchHay(node, info.title, info.tags),
  };
}

function conceptRow(node: ConceptsGraphNode, catalog: LocalizedBookCatalog): ConceptosphereConceptRow {
  const top = node.top_books.slice(0, 5);
  const topBooks = top.map((book) => topBookRow(book, catalog));
  return {
    id: node.id,
    label: node.label,
    community: node.community,
    frequency: node.frequency,
    topBooks,
    searchHay: conceptSearchHay(node, top, topBooks),
  };
}

function topBookRow(
  book: ConceptsGraphNode["top_books"][number],
  catalog: LocalizedBookCatalog,
): ConceptosphereConceptRow["topBooks"][number] {
  const info = requireBookInfo(catalog, book.slug);
  return {
    slug: book.slug,
    title: info.title,
    href: info.href,
    localized: info.localized,
  };
}

function bookSearchHay(node: BooksGraphNode, localizedTitle: string, localizedTags: readonly string[]): string {
  return haystack([
    localizedTitle,
    node.title,
    node.slug,
    String(node.number),
    ...localizedTags,
    ...node.tags,
    ...node.top_concepts.flatMap((concept) => [concept.label, concept.lemma]),
  ]);
}

function conceptSearchHay(
  node: ConceptsGraphNode,
  rawTopBooks: readonly ConceptsGraphNode["top_books"][number][],
  localizedTopBooks: readonly ConceptosphereConceptRow["topBooks"][number][],
): string {
  const topBookTitles = new Set([
    ...rawTopBooks.map((book) => book.title),
    ...localizedTopBooks.map((book) => book.title),
  ]);
  return haystack([node.label, node.lemma, ...topBookTitles]);
}

function haystack(parts: readonly string[]): string {
  return parts.join(" ").toLowerCase();
}

function requireBookInfo(catalog: LocalizedBookCatalog, graphSlug: string): LocalizedBookCatalogEntry {
  const info = catalog.get(graphSlug);
  if (!info) {
    throw new Error(`conceptosphere page data missing resolved book info for graph slug ${JSON.stringify(graphSlug)}`);
  }
  return info;
}

function modeCounts(
  graphs: ConceptosphereGraphs,
  strings: ConceptosphereStrings,
): Record<ConceptosphereMode, string> {
  return {
    concepts: countLine(graphMeasure(graphs.concepts), "concepts", strings),
    books: countLine(graphMeasure(graphs.books), "books", strings),
  };
}

function graphMeasure(graph: BooksGraph | ConceptsGraph): {
  stats: Record<string, unknown>;
  nodesLength: number;
  edgesLength: number;
  communitiesLength: number;
} {
  return {
    stats: graph.stats,
    nodesLength: graph.nodes.length,
    edgesLength: graph.edges.length,
    communitiesLength: graph.communities.length,
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
