import type { ImageMetadata } from "astro";

import type { CoverAssetRegistry } from "./cover-assets";

// Single source of the eager cover glob, shared by every server-rendered cover
// surface (the book-card frame, the poem frontispiece, the project masthead), so
// they cannot drift to different file sets. `astro:assets` optimizes the rasters;
// lookup lives in `cover-assets.ts` (`resolveCoverAsset`).
export const COVER_ASSET_REGISTRY: CoverAssetRegistry = import.meta.glob<ImageMetadata>(
  "/src/content/**/cover.*.{jpg,jpeg,png,webp,avif}",
  { eager: true, import: "default" },
);
