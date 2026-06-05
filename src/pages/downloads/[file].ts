import { existsSync, readFileSync } from "node:fs";
import { resolve as resolvePath } from "node:path";

import type { APIRoute, GetStaticPaths } from "astro";

type ArchiveManifest = {
  archives?: { name?: string }[];
};

const repoRoot = process.cwd();
const manifestPath = resolvePath(repoRoot, "data", "bulk-archives.json");
const cacheDir = resolvePath(repoRoot, ".cache", "bulk-archives");

function archiveNames(): string[] {
  if (!existsSync(manifestPath)) return [];
  const manifest = JSON.parse(readFileSync(manifestPath, "utf-8")) as ArchiveManifest;
  return (manifest.archives ?? [])
    .map(a => a.name)
    .filter((name): name is string => name?.endsWith(".zip") === true);
}

export const getStaticPaths = (() => {
  return archiveNames().map(file => ({ params: { file } }));
}) satisfies GetStaticPaths;

export const GET: APIRoute = ({ params }) => {
  const file = params.file;
  if (!file || !archiveNames().includes(file)) {
    return new Response("Not found", { status: 404 });
  }
  const path = resolvePath(cacheDir, file);
  if (!existsSync(path)) {
    return new Response("Archive has not been generated. Run `npm run generate:bulk-archives`.", {
      status: 404,
      headers: { "Content-Type": "text/plain; charset=utf-8" },
    });
  }
  return new Response(new Uint8Array(readFileSync(path)), {
    headers: {
      "Content-Type": "application/zip",
      "Content-Disposition": `attachment; filename="${file}"`,
    },
  });
};
