import { readFileSync } from "node:fs";
import { resolve as resolvePath } from "node:path";

export interface ArchiveInfo {
  name:       string;
  format:     "md" | "pdf" | "epub";
  url:        string;
  size:       number;
  size_human: string;
  sha256:     string;
  items:      number;
}

export function loadArchiveManifest(): {
  archives: ArchiveInfo[];
  generatedAt: string | null;
} {
  try {
    const manifestPath = resolvePath(process.cwd(), "data", "bulk-archives.json");
    const raw = JSON.parse(readFileSync(manifestPath, "utf-8")) as {
      generated_at: string;
      archives: ArchiveInfo[];
    };
    return { archives: raw.archives, generatedAt: raw.generated_at };
  } catch {
    return { archives: [], generatedAt: null };
  }
}
