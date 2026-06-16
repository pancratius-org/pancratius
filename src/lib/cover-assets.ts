import type { ImageMetadata } from "astro";

import type { Locale } from "./i18n";
import { originFor } from "./origins";
import { COLLECTION_OF, resolveCover, workBundleKey, type WorkPair } from "./works";

export type CoverAsset =
  | { kind: "raster"; image: ImageMetadata }
  | { kind: "svg"; url: string };

export interface CoverAssetRegistry {
  raster: Partial<Record<string, ImageMetadata>>;
  svg: Partial<Record<string, string>>;
}

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
  const image = registry.raster[key];
  if (image) return { kind: "raster", image };

  const url = registry.svg[key];
  if (url) return { kind: "svg", url };

  return null;
}

function coverAssetUrl(asset: CoverAsset): string {
  return asset.kind === "raster" ? asset.image.src : asset.url;
}

export function absoluteCoverAssetUrl(asset: CoverAsset, locale: Locale): string {
  return new URL(coverAssetUrl(asset), originFor(locale)).toString();
}
