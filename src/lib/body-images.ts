import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { extname, resolve as resolvePath } from "node:path";

import type { APIRoute } from "astro";
import sharp from "sharp";

import type { RoutedKind } from "./i18n";
import { CORPUS_WORK_KINDS, ROUTED_KINDS, SEGMENT_OF, type CorpusWorkKind, type RoutedSegment } from "./kinds";
import { publicWorkMarkdownAssetPaths } from "./publication/public-markdown";

const REPO_ROOT = process.cwd();
const CONTENT = resolvePath(REPO_ROOT, "src", "content");

// Disk cache for bounded `/assets/` renditions. `.cache/` is gitignored, so
// this never pollutes the tree and survives across dev/build invocations.
const RENDITION_CACHE_DIR = resolvePath(REPO_ROOT, ".cache", "asset-renditions");

// The `/assets/` endpoint serves a BOUNDED, SAME-FORMAT rendition of the
// archival master that lives under `src/content/**`. The committed source is
// never touched — only the served bytes are resized/recompressed at build time
// so downloaded public Markdown and mirrors get a sane image instead of a
// full-resolution original.
const MAX_LONGEST_EDGE = 1600;
const JPEG_QUALITY = 82;
const WEBP_QUALITY = 80;
const AVIF_QUALITY = 50;

// Rasters we resize/recompress through sharp. Vector (svg) and animated (gif)
// formats are passed through untouched: rasterizing svg would change its
// nature, and animated gif handling through sharp is fragile.
const RASTER_EXTS = new Set([".jpg", ".jpeg", ".png", ".webp", ".avif"]);

// Bump when the rendition algorithm changes so stale cache entries are ignored.
const RENDITION_VERSION = "v1";

const ROUTED_SEGMENT: Record<RoutedKind, RoutedSegment> = SEGMENT_OF;

// Body images live under work bundles AND under the `pages` collection, so the
// scannable set of content dirs is the work segments plus "pages".
const ASSET_KIND_SEGMENTS = [
  ...ROUTED_KINDS.map((kind) => ROUTED_SEGMENT[kind]),
  "pages",
] as const;

const CORPUS_ASSET_SEGMENTS = new Set<string>(
  CORPUS_WORK_KINDS.map((kind) => ROUTED_SEGMENT[kind]),
);

const CONTENT_TYPE: Record<string, string> = {
  ".avif": "image/avif",
  ".gif":  "image/gif",
  ".jpg":  "image/jpeg",
  ".jpeg": "image/jpeg",
  ".png":  "image/png",
  ".svg":  "image/svg+xml",
  ".webp": "image/webp",
};

export interface BodyImageRouteProps {
  [key: string]: unknown;
  diskPath:     string;
  contentType: string;
}

export function workAssetImageStaticPaths() {
  const paths: {
    params: { kind: string; work: string; file: string };
    props: BodyImageRouteProps;
  }[] = [];
  const referencedWorkAssets = referencedWorkAssetPublicPaths();

  for (const kind of ASSET_KIND_SEGMENTS) {
    paths.push(...assetImageStaticPathsForKind(kind, referencedWorkAssets));
  }

  return paths;
}

function assetImageStaticPathsForKind(
  kind: string,
  referencedWorkAssets: ReadonlySet<string>,
): {
  params: { kind: string; work: string; file: string };
  props: BodyImageRouteProps;
}[] {
  const kindRoot = resolvePath(CONTENT, kind);
  if (!existsSync(kindRoot)) return [];
  return readdirSync(kindRoot, { withFileTypes: true })
    .filter((work) => work.isDirectory())
    .flatMap((work) => assetImageStaticPathsForWork(kind, kindRoot, work.name, referencedWorkAssets));
}

function assetImageStaticPathsForWork(
  kind: string,
  kindRoot: string,
  work: string,
  referencedWorkAssets: ReadonlySet<string>,
): {
  params: { kind: string; work: string; file: string };
  props: BodyImageRouteProps;
}[] {
  const imagesDir = resolvePath(kindRoot, work, "images");
  if (!existsSync(imagesDir)) return [];
  return imageFiles(imagesDir).flatMap((file) =>
    assetImageStaticPathForFile(kind, work, imagesDir, file, referencedWorkAssets));
}

function assetImageStaticPathForFile(
  kind: string,
  work: string,
  imagesDir: string,
  file: string,
  referencedWorkAssets: ReadonlySet<string>,
): {
  params: { kind: string; work: string; file: string };
  props: BodyImageRouteProps;
}[] {
  const contentType = CONTENT_TYPE[extname(file).toLowerCase()];
  if (!contentType) return [];
  if (CORPUS_ASSET_SEGMENTS.has(kind) && !referencedWorkAssets.has(assetPublicPath(kind, work, file))) return [];
  return [{
    params: { kind, work, file },
    props: {
      diskPath: resolvePath(imagesDir, file),
      contentType,
    },
  }];
}

function referencedWorkAssetPublicPaths(): Set<string> {
  const paths = new Set<string>();

  for (const kind of CORPUS_WORK_KINDS) {
    for (const path of referencedWorkAssetPublicPathsForKind(kind)) paths.add(path);
  }

  return paths;
}

function referencedWorkAssetPublicPathsForKind(kind: CorpusWorkKind): string[] {
  const segment = ROUTED_SEGMENT[kind];
  const root = resolvePath(CONTENT, segment);
  if (!existsSync(root)) return [];
  return readdirSync(root, { withFileTypes: true })
    .filter((work) => work.isDirectory())
    .flatMap((work) => referencedWorkAssetPublicPathsForWork(kind, root, work.name));
}

function referencedWorkAssetPublicPathsForWork(
  kind: CorpusWorkKind,
  root: string,
  work: string,
): string[] {
  const workDir = resolvePath(root, work);
  return readdirSync(workDir, { withFileTypes: true })
    .filter((entry) => entry.isFile() && entry.name.endsWith(".md"))
    .flatMap((entry) => {
      const source = readFileSync(resolvePath(workDir, entry.name), "utf-8");
      return publicWorkMarkdownAssetPaths(source, { kind, bundleKey: work });
    });
}

function assetPublicPath(kind: string, work: string, file: string): string {
  return `assets/${kind}/${work}/images/${file}`;
}

export const bodyImageGET: APIRoute<BodyImageRouteProps> = async ({ props }) => {
  if (!existsSync(props.diskPath)) {
    return new Response("Not found", { status: 404 });
  }

  const bytes = await renderBodyImage(props.diskPath);
  return new Response(bytes, {
    headers: {
      "Content-Type": props.contentType,
    },
  });
};

// Return a bounded rendition of the source image, caching the result on disk.
// Same output format/extension as the source (so the URL's Content-Type is
// unchanged). Falls back to the raw bytes on any sharp failure so a single bad
// image never fails the build.
async function renderBodyImage(diskPath: string): Promise<Uint8Array<ArrayBuffer>> {
  const ext = extname(diskPath).toLowerCase();

  // svg/gif and any non-raster: pass through untouched.
  if (!RASTER_EXTS.has(ext)) {
    return new Uint8Array(readFileSync(diskPath));
  }

  const stat = statSync(diskPath);
  const cacheKey = createHash("sha256")
    .update(
      [
        RENDITION_VERSION,
        diskPath,
        String(stat.mtimeMs),
        String(stat.size),
        String(MAX_LONGEST_EDGE),
        ext,
        `${JPEG_QUALITY}/${WEBP_QUALITY}/${AVIF_QUALITY}`,
      ].join("\0"),
    )
    .digest("hex");
  const cachePath = resolvePath(RENDITION_CACHE_DIR, `${cacheKey}${ext}`);

  if (existsSync(cachePath)) {
    return new Uint8Array(readFileSync(cachePath));
  }

  try {
    const rendition = await encodeRendition(diskPath, ext);
    mkdirSync(RENDITION_CACHE_DIR, { recursive: true });
    writeFileSync(cachePath, rendition);
    return new Uint8Array(rendition);
  } catch {
    // Never 500 a build over one image: serve the pristine source bytes.
    return new Uint8Array(readFileSync(diskPath));
  }
}

async function encodeRendition(diskPath: string, ext: string): Promise<Buffer> {
  // `withoutEnlargement` guarantees small images are never upscaled.
  const pipeline = sharp(diskPath).resize({
    width: MAX_LONGEST_EDGE,
    height: MAX_LONGEST_EDGE,
    fit: "inside",
    withoutEnlargement: true,
  });

  switch (ext) {
    case ".jpg":
    case ".jpeg":
      return pipeline.jpeg({ quality: JPEG_QUALITY, mozjpeg: true }).toBuffer();
    case ".png":
      return pipeline.png({ palette: true }).toBuffer();
    case ".webp":
      return pipeline.webp({ quality: WEBP_QUALITY }).toBuffer();
    case ".avif":
      return pipeline.avif({ quality: AVIF_QUALITY }).toBuffer();
    default:
      // Unreachable: callers gate on RASTER_EXTS. Defensive passthrough.
      return readFileSync(diskPath);
  }
}

function imageFiles(root: string, prefix = ""): string[] {
  const files: string[] = [];
  for (const dirent of readdirSync(resolvePath(root, prefix), { withFileTypes: true })) {
    const rel = prefix ? `${prefix}/${dirent.name}` : dirent.name;
    if (dirent.isDirectory()) {
      files.push(...imageFiles(root, rel));
    } else if (dirent.isFile()) {
      files.push(rel);
    }
  }
  return files;
}
