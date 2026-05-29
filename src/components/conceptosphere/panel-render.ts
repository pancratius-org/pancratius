import type Graph from "graphology";

import { localizePath } from "@/lib/i18n";

import type { BookSimilarRef, PageConfig, SimilarRef } from "./runtime-types";

interface PanelContext {
  cfg: PageConfig;
  panel: HTMLElement;
  panelBody: HTMLElement;
}

interface PanelSession {
  mode: "concepts" | "books";
  graph: Graph;
  comLabel: Map<number, string>;
  comColor: Map<number, string>;
}

export function showPanel(ctx: PanelContext, s: PanelSession, nodeId: string, pinned: boolean): void {
  const attrs = s.graph.getNodeAttributes(nodeId) as Record<string, unknown>;
  ctx.panelBody.innerHTML = s.mode === "books"
    ? renderBookPanel(ctx, s, attrs)
    : renderConceptPanel(ctx, s, attrs, nodeId);
  wirePanelImageFallbacks(ctx.panelBody);
  ctx.panel.classList.add("is-open");
  ctx.panel.classList.toggle("is-pinned", pinned);
}

export function hidePanel(ctx: PanelContext): void {
  ctx.panel.classList.remove("is-open", "is-pinned");
}

function wirePanelImageFallbacks(root: HTMLElement): void {
  root.querySelectorAll<HTMLImageElement>("img.cs-cover, img.cs-big-cover").forEach((img) => {
    img.addEventListener("error", () => {
      img.classList.add("is-miss");
      img.removeAttribute("src");
    }, { once: true });
  });
}

function renderConceptPanel(
  ctx: PanelContext,
  s: PanelSession,
  a: Record<string, unknown>,
  nodeId: string,
): string {
  const strings = ctx.cfg.strings;
  const numberLocale = strings.numberLocale;
  const community = a.community as number;
  const comName = s.comLabel.get(community)
    ?? strings.clusterFallbackLabel.replace("{n}", String(community));
  const sw = s.comColor.get(community) ?? "#888";
  const top = (a.top_books as { slug: string; title: string; count?: number }[] | undefined) ?? [];
  const books = top.slice(0, 5).map((b) => {
    const coverUrl = ctx.cfg.coverUrls[`book:${b.slug}`];
    const cover = coverUrl
      ? `<img class="cs-cover" loading="lazy" src="${attr(coverUrl)}" alt="" />`
      : `<span class="cs-cover is-miss" aria-hidden="true"></span>`;
    return `<li>
      ${cover}
      <div class="cs-b-meta">
        <a class="cs-b-title" href="${attr(bookHrefFromCfg(b.slug, ctx.cfg))}">${escapeHtml(b.title)}</a>
        <div class="cs-b-count">${(b.count ?? 0).toLocaleString(numberLocale)} ${escapeHtml(strings.mentionsSuffix)}</div>
      </div>
    </li>`;
  }).join("");
  const freq = (a.frequency as number | undefined) ?? 0;
  const cent = (a.centrality as number | undefined) ?? 0;
  const label = typeof a.label === "string" ? a.label : "";
  return `
    <div class="cs-com-tag"><span class="cs-sw" style="background:${attr(sw)}"></span><span>${escapeHtml(comName)}</span></div>
    <h3>${escapeHtml(label)}</h3>
    <dl class="cs-stats">
      <dt>${escapeHtml(strings.statFrequency)}</dt><dd>${freq.toLocaleString(numberLocale)}</dd>
      <dt>${escapeHtml(strings.statCentrality)}</dt><dd>${(cent * 1000).toFixed(2)}‰</dd>
      <dt>${escapeHtml(strings.statConnections)}</dt><dd>${s.graph.degree(nodeId)}</dd>
    </dl>
    <p class="cs-books-h">${escapeHtml(strings.conceptTopBooksHeading)}</p>
    <ol class="cs-books">${books}</ol>`;
}

function renderBookPanel(
  ctx: PanelContext,
  s: PanelSession,
  a: Record<string, unknown>,
): string {
  const strings = ctx.cfg.strings;
  const numberLocale = strings.numberLocale;
  const community = a.community as number;
  const comName = s.comLabel.get(community)
    ?? strings.clusterFallbackLabel.replace("{n}", String(community));
  const sw = s.comColor.get(community) ?? "#888";
  const slug = (a.slug as string | undefined) ?? "";
  const coverUrl = ctx.cfg.coverUrls[`book:${slug}`];
  const selfLink = bookHrefFromCfg(slug, ctx.cfg);
  const coverEl = coverUrl
    ? `<a class="cs-big-cover-link" href="${attr(selfLink)}" aria-label="${attr(strings.openBookLabel)}"><img class="cs-big-cover" loading="lazy" src="${attr(coverUrl)}" alt="" /></a>`
    : `<a class="cs-big-cover-link" href="${attr(selfLink)}" aria-label="${attr(strings.openBookLabel)}"><span class="cs-big-cover is-miss" aria-hidden="true"></span></a>`;

  const tags = ((a.tags as string[] | undefined) ?? []);
  const tagsHtml = tags.length
    ? `<ul class="cs-tags">${tags.map((t) => `<li>${escapeHtml(t)}</li>`).join("")}</ul>`
    : "";

  const topConcepts = ((a.top_concepts as { label?: string; lemma?: string; count?: number }[] | undefined) ?? []);
  const conceptsHtml = topConcepts.slice(0, 10).map((tc) => `
    <li>${escapeHtml(tc.label ?? tc.lemma ?? "")}<span class="cs-n">${(tc.count ?? 0).toLocaleString(numberLocale)}</span></li>
  `).join("");

  // Similar rows are intentionally book-only: the semantic payload can mention
  // poems, but this panel has a stable href/cover contract only for
  // books. Keep that boundary explicit instead of synthesizing mixed-kind URLs.
  const tfItems = ((a.top_similar as SimilarRef[] | undefined) ?? []).filter(isBookSimilar).slice(0, 5);
  const semItems = ((a.top_similar_embed as SimilarRef[] | undefined) ?? []).filter(isBookSimilar).slice(0, 5);
  const semSlugs = new Set(semItems.map((b) => b.slug));
  const tfSlugs = new Set(tfItems.map((b) => b.slug));

  const tfHtml = tfItems.map((b) => renderSimilarRow(ctx, b, semSlugs)).join("");
  const semHtml = semItems.map((b) => renderSimilarRow(ctx, b, tfSlugs)).join("");
  const hasAny = tfItems.length || semItems.length;

  const similarBlock = hasAny ? `
    ${tfItems.length ? `
      <p class="cs-books-h">${escapeHtml(strings.similarByConceptsHeading)}</p>
      <ol class="cs-books cs-similar-tf">${tfHtml}</ol>` : ""}
    ${semItems.length ? `
      <p class="cs-books-h">${escapeHtml(strings.similarByMeaningHeading)}</p>
      <ol class="cs-books cs-similar-sem">${semHtml}</ol>` : ""}
    ${(tfItems.length && semItems.length) ? `<p class="cs-conv-foot"><span class="cs-conv" aria-hidden="true">★</span>${escapeHtml(strings.convergenceFoot)}</p>` : ""}
  ` : "";

  return `
    <div class="cs-com-tag"><span class="cs-sw" style="background:${attr(sw)}"></span><span>${escapeHtml(comName)}</span></div>
    <div class="cs-book-hero">
      ${coverEl}
      <div class="cs-h-meta">
        <div class="cs-h-number">${escapeHtml(strings.bookNumberPrefix)} ${(a.number as number | undefined) ?? "—"}</div>
        <h3 class="cs-book-title-heading"><a class="cs-book-title-link" href="${attr(selfLink)}">${escapeHtml((a.title as string | undefined) ?? (a.label as string | undefined) ?? "")}</a></h3>
        ${tagsHtml}
      </div>
    </div>
    <p class="cs-books-h">${escapeHtml(strings.bookTopConceptsHeading)}</p>
    <ul class="cs-concepts">${conceptsHtml}</ul>
    ${similarBlock}`;
}

function renderSimilarRow(
  ctx: PanelContext,
  ref: BookSimilarRef,
  convergentSet: Set<string>,
): string {
  const strings = ctx.cfg.strings;
  const key = `book:${ref.slug}`;
  const coverUrl = ctx.cfg.coverUrls[key];
  const cover = coverUrl
    ? `<img class="cs-cover" loading="lazy" src="${attr(coverUrl)}" alt="" />`
    : `<span class="cs-cover is-miss" aria-hidden="true"></span>`;
  const star = convergentSet.has(ref.slug)
    ? `<span class="cs-conv" title="${attr(strings.convergenceLabel)}" aria-label="${attr(strings.convergenceLabel)}">★</span>`
    : "";
  const pct = ((ref.weight ?? 0) * 100).toFixed(0);
  const href = bookHrefFromCfg(ref.slug, ctx.cfg);
  const caption = strings.similarityCaption.replace("{pct}", pct);
  return `<li>
    ${cover}
    <div class="cs-b-meta">
      <div class="cs-b-title-row"><a class="cs-b-title" href="${attr(href)}">${escapeHtml(ref.title)}</a>${star}</div>
      <div class="cs-b-count">${escapeHtml(caption)}</div>
    </div>
  </li>`;
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

function attr(s: string): string {
  return escapeHtml(s);
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, escapeHtmlChar);
}

function escapeHtmlChar(c: string): string {
  switch (c) {
    case "&": return "&amp;";
    case "<": return "&lt;";
    case ">": return "&gt;";
    case "\"": return "&quot;";
    case "'": return "&#39;";
    default: return c;
  }
}
