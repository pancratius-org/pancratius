import type { ImageMetadata } from "astro";

import type { CoverAssetRegistry } from "./cover-assets";

// Single source of the eager cover-asset glob, shared by every server-rendered
// cover surface (the book-card frame, the poem frontispiece). `astro:assets`
// optimizes rasters; SVG covers ship as plain URLs. Lookup + variant resolution
// live in `cover-assets.ts` (`resolveCoverAsset`); this module only owns the glob
// so the two surfaces cannot drift to different file sets.
export const COVER_ASSET_REGISTRY = {
  raster: import.meta.glob<ImageMetadata>(
    "/src/content/**/cover.*.{jpg,jpeg,png,webp,avif}",
    { eager: true, import: "default" },
  ),
  svg: import.meta.glob<string>(
    "/src/content/**/cover.*.svg",
    { eager: true, query: "?url", import: "default" },
  ),
} satisfies CoverAssetRegistry;
