import type { Locale } from "@/lib/i18n";

import { CONCEPTOSPHERE_MODES, isConceptosphereMode, type ConceptosphereMode } from "./graph-types.ts";
import type { ConceptosphereStrings } from "./strings.ts";

export interface PageConfig {
  conceptsUrl: string;
  booksUrl: string;
  initialMode: ConceptosphereMode;
  locale: Locale;
  modeCounts: Record<ConceptosphereMode, string>;
  /**
   * Per-book lookup keyed by the RU slug (the graph payload's identity).
   * `href` is resolved at build time, so runtime code never has to invent
   * locale-specific book URLs.
   */
  bookSlugInfo: Record<string, { number: number; title: string; href: string; localized: boolean; tags: readonly string[] }>;
  coverUrls: Record<string, string>;
  strings: ConceptosphereStrings;
}

export function readPageConfig(): PageConfig | null {
  const raw = document.getElementById("cs-config")?.textContent;
  if (!raw) return null;

  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!isPageConfig(parsed)) {
      console.error("conceptosphere: invalid #cs-config payload");
      return null;
    }
    return parsed;
  } catch (err) {
    console.error("conceptosphere: failed to parse #cs-config", err);
    return null;
  }
}

function isPageConfig(value: unknown): value is PageConfig {
  if (!isRecord(value)) return false;
  return typeof value.conceptsUrl === "string"
    && typeof value.booksUrl === "string"
    && isConceptosphereMode(value.initialMode)
    && typeof value.locale === "string"
    && isModeStringRecord(value.modeCounts)
    && isBookSlugInfo(value.bookSlugInfo)
    && isStringRecord(value.coverUrls)
    && isConceptosphereStrings(value.strings);
}

function isConceptosphereStrings(value: unknown): value is ConceptosphereStrings {
  if (!isRecord(value) || !isRecord(value.modes)) return false;
  return typeof value.numberLocale === "string"
    && hasModeCopy(value.modes, "concepts")
    && hasModeCopy(value.modes, "books")
    && [
      "legendTitle",
      "clusterFallbackLabel",
      "statFrequency",
      "statCentrality",
      "statConnections",
      "conceptTopBooksHeading",
      "mentionsSuffix",
      "bookTopConceptsHeading",
      "similarByConceptsHeading",
      "similarByMeaningHeading",
      "convergenceFoot",
      "convergenceLabel",
      "openBookLabel",
      "russianOriginalBadge",
      "openInRussianLabel",
      "similarityCaption",
      "bookNumberPrefix",
      "loadErrorPrefix",
    ].every((key) => typeof value[key] === "string");
}

function hasModeCopy(value: Record<string, unknown>, mode: ConceptosphereMode): boolean {
  const copy = value[mode];
  return isRecord(copy)
    && typeof copy.h1 === "string"
    && typeof copy.lede === "string"
    && typeof copy.meth === "string"
    && typeof copy.searchPlaceholder === "string";
}

function isModeStringRecord(value: unknown): value is Record<ConceptosphereMode, string> {
  return isRecord(value) && CONCEPTOSPHERE_MODES.every((mode) => typeof value[mode] === "string");
}

function isBookSlugInfo(value: unknown): value is PageConfig["bookSlugInfo"] {
  if (!isRecord(value)) return false;
  return Object.values(value).every((item) =>
    isRecord(item)
    && typeof item.number === "number"
    && Number.isFinite(item.number)
    && typeof item.title === "string"
    && typeof item.href === "string"
    && typeof item.localized === "boolean"
    && Array.isArray(item.tags)
    && item.tags.every((tag) => typeof tag === "string"),
  );
}

function isStringRecord(value: unknown): value is Record<string, string> {
  return isRecord(value) && Object.values(value).every((item) => typeof item === "string");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
