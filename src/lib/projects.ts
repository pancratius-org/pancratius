// Project discovery + helpers.
//
// Projects are themed mini-sites / sections, NOT downloadable works. They live
// in their own `projects` content collection as a discriminated union of a
// landing (`kind: "project"`) and its sub-pages (`kind: "project_subpage"`),
// and are deliberately kept OUT of the work-pair / download machinery in
// `src/lib/works.ts`.
//
// The single cross-module dependency runs one way: a project's `featured_books`
// reference BOOKS by editorial number, which `resolveFeaturedBooks` resolves
// through `works.ts` (`findPair("book", n)`). Nothing in `works.ts` depends on
// this module.

import { getCollection, type CollectionEntry } from "astro:content";

import type { Locale } from "./i18n";
import { LOCALES, localizePath, workUrl } from "./i18n";
import { findPair, type WorkPair } from "./works";

type ProjectCollectionEntry = CollectionEntry<"projects">;

// `CollectionEntry<"projects">` is a single record type whose `data` is the
// whole discriminated union (landing | subpage), so we narrow on `data`, not on
// the entry — then re-attach the narrowed data to the entry shape.
type ProjectData = ProjectCollectionEntry["data"];
type LandingData = Extract<ProjectData, { kind: "project" }>;
type SubpageData = Extract<ProjectData, { kind: "project_subpage" }>;

/** A project LANDING entry (the `kind: "project"` arm of the union). */
export type ProjectLanding = Omit<ProjectCollectionEntry, "data"> & {
  data: LandingData;
};

/** A project SUB-PAGE entry (the `kind: "project_subpage"` arm of the union). */
export type ProjectSubpage = Omit<ProjectCollectionEntry, "data"> & {
  data: SubpageData;
};

function isLanding(e: ProjectCollectionEntry): e is ProjectLanding {
  return e.data.kind === "project";
}

function isSubpage(e: ProjectCollectionEntry): e is ProjectSubpage {
  return e.data.kind === "project_subpage";
}

// Mirror the work cache pattern: cache the loaded collection, but never cache an
// empty result (a dev-server startup race can briefly return [] before content
// is synced, and caching that poisons every later call).
let _cache: ProjectCollectionEntry[] | null = null;

async function loadAll(): Promise<ProjectCollectionEntry[]> {
  if (_cache) return _cache;
  const entries = await getCollection("projects");
  if (entries.length > 0) _cache = entries;
  return entries;
}

// ─────────────────────────────────────────────────────────────────────
// Landing discovery.
// ─────────────────────────────────────────────────────────────────────

/** All project landings authored in `locale`, sorted by editorial number. */
export async function getProjects(locale: Locale): Promise<ProjectLanding[]> {
  const all = await loadAll();
  return all
    .filter(isLanding)
    .filter(e => e.data.lang === locale)
    .sort((a, b) => a.data.number - b.data.number);
}

/** The landing for `slug` in `locale`, or null if not authored in that locale. */
export async function getProject(
  slug: string,
  locale: Locale,
): Promise<ProjectLanding | null> {
  const all = await loadAll();
  return (
    all
      .filter(isLanding)
      .find(e => e.data.slug === slug && e.data.lang === locale) ?? null
  );
}

/**
 * Locales in which a project `slug` has an authored landing. Lets a route emit
 * an `/en/projects/<slug>/` page only when an EN landing actually exists, so a
 * locale never renders another locale's body.
 */
export async function authoredProjectLocales(slug: string): Promise<Locale[]> {
  const out: Locale[] = [];
  for (const loc of LOCALES) {
    if (await getProject(slug, loc)) out.push(loc);
  }
  return out;
}

// ─────────────────────────────────────────────────────────────────────
// Sub-page discovery.
// ─────────────────────────────────────────────────────────────────────

/**
 * Sub-pages of `projectSlug` authored in `locale`. Ordered by the landing's
 * `subpages` list when present (authoring order is the editorial order), and by
 * slug otherwise. Returns [] when the project has no authored sub-pages yet.
 */
export async function getProjectSubpages(
  projectSlug: string,
  locale: Locale,
): Promise<ProjectSubpage[]> {
  const all = await loadAll();
  const subs = all
    .filter(isSubpage)
    .filter(e => e.data.parent === projectSlug && e.data.lang === locale);

  const landing = await getProject(projectSlug, locale);
  const order = landing?.data.subpages?.map(s => s.slug) ?? [];
  if (order.length === 0) {
    return subs.sort((a, b) => a.data.slug.localeCompare(b.data.slug));
  }
  const rank = new Map(order.map((slug, i) => [slug, i]));
  return subs.sort((a, b) => {
    const ra = rank.get(a.data.slug) ?? Number.MAX_SAFE_INTEGER;
    const rb = rank.get(b.data.slug) ?? Number.MAX_SAFE_INTEGER;
    if (ra !== rb) return ra - rb;
    return a.data.slug.localeCompare(b.data.slug);
  });
}

/** A single sub-page by `(projectSlug, subSlug, locale)`, or null if absent. */
export async function getProjectSubpage(
  projectSlug: string,
  subSlug: string,
  locale: Locale,
): Promise<ProjectSubpage | null> {
  const all = await loadAll();
  return (
    all
      .filter(isSubpage)
      .find(
        e =>
          e.data.parent === projectSlug &&
          e.data.slug === subSlug &&
          e.data.lang === locale,
      ) ?? null
  );
}

// ─────────────────────────────────────────────────────────────────────
// Featured books — the one cross-module link (projects → books).
// ─────────────────────────────────────────────────────────────────────

export interface ResolvedFeaturedBook {
  pair:   WorkPair;
  blurb?: string;
}

/**
 * Resolve a landing's `featured_books` ({ number, blurb? }[]) into book
 * `WorkPair`s plus their blurbs, so a strip can render `BookCard`s. A missing
 * book number is a content/integrity bug (also caught by the slug-map audit),
 * so we throw with a clear message rather than silently dropping the card.
 */
export async function resolveFeaturedBooks(
  featured: ReadonlyArray<{ number: number; blurb?: string }> | undefined,
): Promise<ResolvedFeaturedBook[]> {
  if (!featured || featured.length === 0) return [];
  const out: ResolvedFeaturedBook[] = [];
  for (const f of featured) {
    const pair = await findPair("book", f.number);
    if (!pair) {
      throw new Error(
        `resolveFeaturedBooks: book #${f.number} not in corpus`,
      );
    }
    out.push({ pair, blurb: f.blurb });
  }
  return out;
}

/** Resolve the secondary `featured_books_more` (numbers only) into book pairs. */
export async function resolveFeaturedBooksMore(
  numbers: ReadonlyArray<number> | undefined,
): Promise<WorkPair[]> {
  if (!numbers || numbers.length === 0) return [];
  const out: WorkPair[] = [];
  for (const n of numbers) {
    const pair = await findPair("book", n);
    if (!pair) {
      throw new Error(`resolveFeaturedBooksMore: book #${n} not in corpus`);
    }
    out.push(pair);
  }
  return out;
}

// ─────────────────────────────────────────────────────────────────────
// URL helpers. Reuse the i18n layer — `SEGMENT_OF["project"]` still maps to
// `/projects/`, so project URLs are unchanged.
// ─────────────────────────────────────────────────────────────────────

/** Canonical landing URL for a project slug in `locale` (e.g. `/projects/holy-rus/`). */
export function projectUrl(slug: string, locale: Locale): string {
  return workUrl("project", slug, locale);
}

/** Canonical sub-page URL (e.g. `/projects/holy-rus/sobor/`). */
export function projectSubpageUrl(
  projectSlug: string,
  subSlug: string,
  locale: Locale,
): string {
  return localizePath(`/projects/${projectSlug}/${subSlug}/`, locale);
}
