import { localizePath } from "@/lib/i18n";

import { russianOriginalBadge } from "./russian-badge.ts";
import type { CommunityCatalog } from "./graph-data.ts";
import type { ConceptGraph, ConceptNodeAttributes } from "./graph-model.ts";
import type { BookSimilarRef, ConceptosphereMode, SimilarRef, TopConceptRef } from "./graph-types.ts";
import type { PageConfig } from "./page-config.ts";

export interface PanelHost {
  cfg: PageConfig;
  panel: HTMLElement;
  panelBody: HTMLElement;
}

interface PanelSession {
  mode: ConceptosphereMode;
  graph: ConceptGraph;
  communities: CommunityCatalog;
}

const PANEL_CLASS = {
  communityTag: "cs-com-tag",
  swatch: "cs-sw",
  stats: "cs-stats",
  booksHeading: "cs-books-h",
  booksList: "cs-books",
  cover: "cs-cover",
  missing: "is-miss",
  bookMeta: "cs-b-meta",
  bookTitle: "cs-b-title",
  bookCount: "cs-b-count",
  bookHero: "cs-book-hero",
  bookCoverLink: "cs-big-cover-link",
  bigCover: "cs-big-cover",
  heroMeta: "cs-h-meta",
  heroNumber: "cs-h-number",
  bookTitleHeading: "cs-book-title-heading",
  bookTitleLink: "cs-book-title-link",
  tags: "cs-tags",
  concepts: "cs-concepts",
  count: "cs-n",
  similarTf: "cs-similar-tf",
  similarSem: "cs-similar-sem",
  titleRow: "cs-b-title-row",
  convergence: "cs-conv",
  convergenceFoot: "cs-conv-foot",
  shared: "cs-shared",
} as const;

export function showPanel(ctx: PanelHost, session: PanelSession, nodeId: string, pinned: boolean): void {
  const attrs = session.graph.getNodeAttributes(nodeId);
  ctx.panelBody.replaceChildren(
    session.mode === "books"
      ? renderBookPanel(ctx, session, attrs)
      : renderConceptPanel(ctx, session, attrs, nodeId),
  );
  wirePanelImageFallbacks(ctx.panelBody);
  ctx.panel.classList.add("is-open");
  ctx.panel.classList.toggle("is-pinned", pinned);
}

export function hidePanel(ctx: PanelHost): void {
  ctx.panel.classList.remove("is-open", "is-pinned");
}

function wirePanelImageFallbacks(root: HTMLElement): void {
  root.querySelectorAll<HTMLImageElement>("img.cs-cover, img.cs-big-cover").forEach((img) => {
    img.addEventListener("error", () => {
      img.classList.add(PANEL_CLASS.missing);
      img.removeAttribute("src");
    }, { once: true });
  });
}

function renderConceptPanel(
  ctx: PanelHost,
  session: PanelSession,
  attrs: ConceptNodeAttributes,
  nodeId: string,
): DocumentFragment {
  const strings = ctx.cfg.strings;
  const numberLocale = strings.numberLocale;
  const community = communityView(ctx, session, attrs.communityId);

  return fragment(
    communityTag(community),
    element("h3", { text: attrs.label }),
    statsList([
      [strings.statFrequency, (attrs.frequency ?? 0).toLocaleString(numberLocale)],
      [strings.statCentrality, `${((attrs.centrality ?? 0) * 1000).toFixed(2)}‰`],
      [strings.statConnections, String(session.graph.degree(nodeId))],
    ]),
    element("p", { className: PANEL_CLASS.booksHeading, text: strings.conceptTopBooksHeading }),
    orderedBookList(
      attrs.topBooks.slice(0, 5).map((book) => conceptBookRow(ctx, book, numberLocale)),
    ),
  );
}

function renderBookPanel(
  ctx: PanelHost,
  session: PanelSession,
  attrs: ConceptNodeAttributes,
): DocumentFragment {
  const strings = ctx.cfg.strings;
  const community = communityView(ctx, session, attrs.communityId);
  const slug = attrs.slug ?? "";
  const selfLink = bookHrefFromCfg(slug, ctx.cfg);

  return fragment(
    communityTag(community),
    bookHero(ctx, attrs, selfLink),
    communityConceptBlock(strings.clusterTopConceptsHeading, community.topConcepts, strings.numberLocale),
    element("p", { className: PANEL_CLASS.booksHeading, text: strings.bookTopConceptsHeading }),
    conceptList(attrs.topConcepts, strings.numberLocale),
    similarBooksBlock(ctx, attrs),
  );
}

function conceptBookRow(
  ctx: PanelHost,
  book: { slug: string; title: string; count?: number },
  numberLocale: string,
): HTMLLIElement {
  const href = bookHrefFromCfg(book.slug, ctx.cfg);
  // A RU-only book in a concept's top-books list on /en/ falls back to its
  // Russian page; it carries the same shared badge as every other fallback site.
  const localized = ctx.cfg.bookSlugInfo[book.slug]?.localized ?? true;
  const titleLink = link(href, book.title, PANEL_CLASS.bookTitle);

  const meta: Node[] = [titleLink];
  meta.push(element("div", {
    className: PANEL_CLASS.bookCount,
    text: `${(book.count ?? 0).toLocaleString(numberLocale)} ${ctx.cfg.strings.mentionsSuffix}`,
  }));
  if (!localized) meta.push(russianOriginalBadge(ctx.cfg.strings));

  return element("li", {}, [
    coverThumb(ctx.cfg.coverUrls[`book:${book.slug}`]),
    element("div", { className: PANEL_CLASS.bookMeta }, meta),
  ]);
}

function bookHero(ctx: PanelHost, attrs: ConceptNodeAttributes, selfLink: string): HTMLElement {
  const strings = ctx.cfg.strings;
  const slug = attrs.slug ?? "";
  const coverUrl = ctx.cfg.coverUrls[`book:${slug}`];
  const info = ctx.cfg.bookSlugInfo[slug];
  // A RU-only book on /en/ links to its Russian page; the link declares that.
  const localized = info?.localized ?? true;
  // Tags come from the resolved-locale frontmatter (EN for an EN-paired book),
  // not the graph node's RU `tags`. RU-only books fall back to their RU tags.
  const tags = info?.tags ?? attrs.tags;
  const coverLink = link(selfLink, "", PANEL_CLASS.bookCoverLink);
  coverLink.setAttribute("aria-label", strings.openBookLabel);
  coverLink.append(bigCover(coverUrl));

  const meta: (Node | string)[] = [
    element("div", {
      className: PANEL_CLASS.heroNumber,
      text: `${strings.bookNumberPrefix} ${attrs.number ?? "—"}`,
    }),
    element("h3", { className: PANEL_CLASS.bookTitleHeading }, [
      link(selfLink, attrs.title ?? attrs.label, PANEL_CLASS.bookTitleLink),
    ]),
    tagList(tags),
  ];
  if (!localized) meta.push(russianOriginalBadge(strings));

  return element("div", { className: PANEL_CLASS.bookHero }, [
    coverLink,
    element("div", { className: PANEL_CLASS.heroMeta }, meta),
  ]);
}

function communityConceptBlock(
  heading: string,
  concepts: readonly TopConceptRef[],
  numberLocale: string,
): DocumentFragment {
  if (!concepts.length) return fragment();
  return fragment(
    element("p", { className: PANEL_CLASS.booksHeading, text: heading }),
    conceptList(concepts.slice(0, 8), numberLocale),
  );
}

function conceptList(concepts: readonly TopConceptRef[], numberLocale: string): HTMLUListElement {
  return element("ul", { className: PANEL_CLASS.concepts }, concepts.slice(0, 10).map((concept) =>
    element("li", {}, [
      conceptLabelNode(concept),
      concept.count === undefined
        ? fragment()
        : element("span", { className: PANEL_CLASS.count, text: concept.count.toLocaleString(numberLocale) }),
    ]),
  ));
}

function similarBooksBlock(ctx: PanelHost, attrs: ConceptNodeAttributes): DocumentFragment {
  const tfItems = attrs.similarByConcepts.filter(isBookSimilar).slice(0, 5);
  const semItems = attrs.similarByMeaning.filter(isBookSimilar).slice(0, 5);
  const tfSlugs = new Set(tfItems.map((book) => book.slug));
  const semSlugs = new Set(semItems.map((book) => book.slug));
  const strings = ctx.cfg.strings;

  const nodes: Node[] = [];
  if (tfItems.length) {
    nodes.push(
      element("p", { className: PANEL_CLASS.booksHeading, text: strings.similarByConceptsHeading }),
      orderedBookList(tfItems.map((book) => similarBookRow(ctx, book, semSlugs)), PANEL_CLASS.similarTf),
    );
  }
  if (semItems.length) {
    nodes.push(
      element("p", { className: PANEL_CLASS.booksHeading, text: strings.similarByMeaningHeading }),
      orderedBookList(semItems.map((book) => similarBookRow(ctx, book, tfSlugs)), PANEL_CLASS.similarSem),
    );
  }
  if (tfItems.length && semItems.length) {
    const marker = element("span", { className: PANEL_CLASS.convergence, text: "★" });
    marker.setAttribute("aria-hidden", "true");
    nodes.push(element("p", { className: PANEL_CLASS.convergenceFoot }, [
      marker,
      strings.convergenceFoot,
    ]));
  }
  return fragment(...nodes);
}

function similarBookRow(
  ctx: PanelHost,
  ref: BookSimilarRef,
  convergentSet: ReadonlySet<string>,
): HTMLLIElement {
  const strings = ctx.cfg.strings;
  const star = element("span", { className: PANEL_CLASS.convergence, text: "★" });
  star.title = strings.convergenceLabel;
  star.setAttribute("aria-label", strings.convergenceLabel);
  if (!convergentSet.has(ref.slug)) star.hidden = true;

  // A RU-only neighbour on /en/ links to its Russian page and declares it with
  // the same shared badge every fallback site uses — never a fake-EN row.
  const href = bookHrefFromCfg(ref.slug, ctx.cfg);
  const localized = ctx.cfg.bookSlugInfo[ref.slug]?.localized ?? true;
  const titleLink = link(href, ref.title, PANEL_CLASS.bookTitle);

  const meta: Node[] = [element("div", { className: PANEL_CLASS.titleRow }, [titleLink, star])];
  meta.push(element("div", {
    className: PANEL_CLASS.bookCount,
    text: strings.similarityCaption.replace("{pct}", ((ref.weight ?? 0) * 100).toFixed(0)),
  }));
  if (ref.shared_concepts?.length) {
    meta.push(sharedConceptLine(strings.sharedConceptsPrefix, ref.shared_concepts.slice(0, 3)));
  }
  if (!localized) meta.push(russianOriginalBadge(strings));

  return element("li", {}, [
    coverThumb(ctx.cfg.coverUrls[`book:${ref.slug}`]),
    element("div", { className: PANEL_CLASS.bookMeta }, meta),
  ]);
}

function sharedConceptLine(prefix: string, concepts: readonly TopConceptRef[]): HTMLElement {
  const children: (Node | string)[] = [prefix];
  concepts.forEach((concept, index) => {
    if (index > 0) children.push(" · ");
    children.push(conceptLabelNode(concept));
  });
  return element("div", { className: PANEL_CLASS.shared }, children);
}

function conceptLabelNode(concept: TopConceptRef): Node | string {
  const label = concept.label ?? concept.lemma ?? "";
  if (!concept.untranslated) return label;
  const span = element("span", { text: label });
  span.lang = "ru";
  return span;
}

function communityTag(community: { label: string; color: string }): HTMLElement {
  const swatch = element("span", { className: PANEL_CLASS.swatch });
  swatch.style.background = community.color;
  return element("div", { className: PANEL_CLASS.communityTag }, [
    swatch,
    element("span", { text: community.label }),
  ]);
}

function statsList(rows: readonly [string, string][]): HTMLDListElement {
  const list = element("dl", { className: PANEL_CLASS.stats });
  for (const [term, description] of rows) {
    list.append(element("dt", { text: term }), element("dd", { text: description }));
  }
  return list;
}

function orderedBookList(rows: readonly HTMLLIElement[], extraClass?: string): HTMLOListElement {
  return element("ol", {
    className: extraClass ? `${PANEL_CLASS.booksList} ${extraClass}` : PANEL_CLASS.booksList,
  }, rows);
}

function tagList(tags: readonly string[]): HTMLUListElement | DocumentFragment {
  if (!tags.length) return fragment();
  return element("ul", { className: PANEL_CLASS.tags }, tags.map((tag) => element("li", { text: tag })));
}

function coverThumb(src: string | undefined): HTMLElement {
  if (!src) return element("span", { className: `${PANEL_CLASS.cover} ${PANEL_CLASS.missing}` });
  const img = element("img", { className: PANEL_CLASS.cover });
  img.loading = "lazy";
  img.src = src;
  img.alt = "";
  return img;
}

function bigCover(src: string | undefined): HTMLElement {
  if (!src) return element("span", { className: `${PANEL_CLASS.bigCover} ${PANEL_CLASS.missing}` });
  const img = element("img", { className: PANEL_CLASS.bigCover });
  img.loading = "lazy";
  img.src = src;
  img.alt = "";
  return img;
}

function communityView(
  ctx: PanelHost,
  session: PanelSession,
  communityId: number,
): { label: string; color: string; topConcepts: readonly TopConceptRef[] } {
  const community = session.communities.byId.get(communityId);
  return {
    label: community?.label ?? ctx.cfg.strings.clusterFallbackLabel.replace("{n}", String(communityId)),
    color: community?.color ?? "#888",
    topConcepts: community?.topConcepts ?? [],
  };
}

function isBookSimilar(ref: SimilarRef): ref is BookSimilarRef {
  return ref.kind === "book";
}

function bookHrefFromCfg(slug: string | undefined | null, cfg: PageConfig): string {
  if (!slug) return localizePath("/books/", cfg.locale);
  const info = cfg.bookSlugInfo[slug];
  if (info?.href) return info.href;
  const clean = encodeURIComponent(slug.trim().replace(/^\/+|\/+$/g, "")).replace(/\./g, "%2E");
  return clean ? `/books/${clean}/` : "/books/";
}

function link(href: string, text: string, className: string): HTMLAnchorElement {
  const anchor = element("a", { className, text });
  anchor.href = href;
  return anchor;
}

function fragment(...children: readonly Node[]): DocumentFragment {
  const out = document.createDocumentFragment();
  out.append(...children);
  return out;
}

function element<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  options: { className?: string; text?: string } = {},
  children: readonly (Node | string)[] = [],
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (options.className) node.className = options.className;
  if (options.text !== undefined) node.textContent = options.text;
  for (const child of children) node.append(typeof child === "string" ? document.createTextNode(child) : child);
  return node;
}
