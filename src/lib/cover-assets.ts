import type { ImageMetadata } from "astro";

import type { Locale } from "./i18n";
import { originFor } from "./origins";
import { COLLECTION_OF, resolveCover, workBundleKey, type WorkPair } from "./works";

/** A resolved cover image. Covers are raster art, optimized by `astro:assets`. */
export type CoverAsset = ImageMetadata;

/** The eager cover glob, keyed by repo-absolute asset path. */
export type CoverAssetRegistry = Partial<Record<string, ImageMetadata>>;

export function contentCoverKey(
  collection: "books" | "poetry" | "projects",
  folder: string,
  rel: string,
): string {
  return `/src/content/${collection}/${folder}/${rel.replace(/^\.\//, "")}`;
}

export function workCoverKey(pair: WorkPair, locale: Locale): string | null {
  const cover = resolveCover(pair, locale);
  if (!cover) return null;
  return contentCoverKey(COLLECTION_OF[pair.kind], workBundleKey(pair), cover.rel);
}

export function resolveCoverAsset(
  key: string,
  registry: CoverAssetRegistry,
): CoverAsset | null {
  return registry[key] ?? null;
}

export function absoluteCoverAssetUrl(asset: CoverAsset, locale: Locale): string {
  return new URL(asset.src, originFor(locale)).toString();
}
