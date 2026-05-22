import { existsSync, readdirSync, readFileSync } from "node:fs";
import { extname, resolve as resolvePath } from "node:path";

import type { APIRoute } from "astro";

import type { WorkKind } from "./i18n";
import type { WorkPair } from "./works";

const REPO_ROOT = process.cwd();
const CONTENT = resolvePath(REPO_ROOT, "src", "content");

const WORK_SEGMENT: Record<WorkKind, AssetKindSegment> = {
  book:    "books",
  poem:    "poetry",
  project: "projects",
};

const ASSET_KIND_SEGMENTS = ["books", "poetry", "projects", "pages"] as const;
type AssetKindSegment = typeof ASSET_KIND_SEGMENTS[number];

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

export function workBundleKey(pair: WorkPair): string {
  return pair.ru.id.split("--")[0];
}

export function workAssetImagePublicPath(kind: WorkKind, workKey: string, imagePath: string): string {
  const normalized = imagePath.trim().replace(/^\.?\//, "");
  if (!normalized.startsWith("images/")) {
    throw new Error(`workAssetImagePublicPath expects an images/ path, got ${JSON.stringify(imagePath)}`);
  }
  return `/assets/${WORK_SEGMENT[kind]}/${workKey}/${normalized}`;
}

export function workAssetImageStaticPaths() {
  const paths: {
    params: { kind: string; work: string; file: string };
    props: BodyImageRouteProps;
  }[] = [];

  for (const kind of ASSET_KIND_SEGMENTS) {
    const kindRoot = resolvePath(CONTENT, kind);
    if (!existsSync(kindRoot)) continue;

    for (const work of readdirSync(kindRoot, { withFileTypes: true })) {
      if (!work.isDirectory()) continue;
      const imagesDir = resolvePath(kindRoot, work.name, "images");
      if (!existsSync(imagesDir)) continue;

      for (const file of imageFiles(imagesDir)) {
        const ext = extname(file).toLowerCase();
        const contentType = CONTENT_TYPE[ext];
        if (!contentType) continue;
        paths.push({
          params: { kind, work: work.name, file },
          props: {
            diskPath: resolvePath(imagesDir, file),
            contentType,
          },
        });
      }
    }
  }

  return paths;
}

export const bodyImageGET: APIRoute<BodyImageRouteProps> = ({ props }) => {
  if (!props || !existsSync(props.diskPath)) {
    return new Response("Not found", { status: 404 });
  }

  return new Response(new Uint8Array(readFileSync(props.diskPath)), {
    headers: {
      "Content-Type": props.contentType,
    },
  });
};

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
