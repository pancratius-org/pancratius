// Filesystem access for rules, parameterised by a root so the SAME rule runs
// against the real repo and against a tiny fixture tree (see selftest.ts).
// Rules never touch `fs` directly — they go through the RuleContext built here,
// which is what makes both-polarity fixture testing possible.

import { readdirSync, readFileSync, existsSync, statSync } from "node:fs";
import { join, resolve, dirname, sep } from "node:path";
import { fileURLToPath } from "node:url";

/** Absolute path to the repository root (scripts/audit/lib/ → repo root). */
export const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");

/** Directory holding the harness (scripts/audit). Python checks resolve here. */
export const AUDIT_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "..");

// The harness's own fixtures tree is the one place known-bad content is allowed
// to live (selftest.ts points rules AT it). A real-repo scan must never see it,
// or every rule would fire on its own negative fixtures. Excluded here, once, by
// absolute path — when a rule runs against a fixture root the walk starts BELOW
// this directory, so the exclusion is inert there. Python checks prune the same
// relative path (`scripts/audit/fixtures`); see _example_check.py.
const HARNESS_FIXTURES_ABS = join(AUDIT_DIR, "fixtures");

// Disposable / vendor / VCS trees that no rule should ever walk by default.
// These are build output and caches, never source — keeping them out of the walk
// is not a contract decision, just "don't scan generated junk". Production-source
// boundaries (legacy/, design/) are NOT hidden here: a rule that cares about them
// derives that from the real source (tsconfig exclude etc.), per PAN016.
// Non-dot build/vendor/report trees — never source. Dot-directories (.git,
// .cache, .astro, .venv, .pytest_cache, .ruff_cache, editor/agent dirs, …) are
// pruned wholesale by the dot-dir rule below, so they don't need listing here.
const ALWAYS_IGNORE: ReadonlySet<string> = new Set([
  "node_modules",
  "dist",
  "__pycache__",
  "playwright-report",
  "test-results",
  "coverage",
]);

// Dot-directories are tooling/metadata and proliferate as new tools appear
// (.venv, .ruff_cache, .ty_cache, …), so the default is to skip every dot-dir
// rather than chase a denylist — except the few a rule legitimately scans (CI
// config under .github, which PAN012 reads). Dot-FILES are kept; rules filter.
// The Python walkers mirror this exact rule (see _example_check.py).
const KEEP_DOT_DIRS: ReadonlySet<string> = new Set([".github"]);

/** Normalise an OS path to forward slashes so rule matching is platform-stable. */
export function toPosix(p: string): string {
  return sep === "/" ? p : p.split(sep).join("/");
}

/** Directory entries, or [] for an unreadable directory. Type inferred from the call. */
function readEntries(absDir: string) {
  try {
    return readdirSync(absDir, { withFileTypes: true });
  } catch {
    return [];
  }
}

export interface WalkOptions {
  /** Extra directory basenames to skip, on top of the always-ignored set. */
  ignoreDirs?: Iterable<string>;
  /**
   * Directory basenames to walk that are ignored by default — the escape a
   * `deploy`-tier rule uses to enumerate an emitted tree, e.g.
   * `walk({ unignore: ["dist"], filter: p => p.endsWith(".html") })`.
   */
  unignore?: Iterable<string>;
  /** Keep only files matching this predicate (receives the POSIX rel path). */
  filter?: (relPath: string) => boolean;
}

/**
 * List every file under `root` as a repo-relative POSIX path, skipping the
 * always-ignored trees and dot-directories. Deterministic order (sorted).
 */
export function walk(root: string, opts: WalkOptions = {}): string[] {
  const skip = new Set(ALWAYS_IGNORE);
  for (const d of opts.ignoreDirs ?? []) skip.add(d);
  const unignore = new Set(opts.unignore ?? []);
  const out: string[] = [];

  const skipDir = (name: string): boolean => {
    if (unignore.has(name)) return false;
    if (skip.has(name)) return true;
    return name.startsWith(".") && !KEEP_DOT_DIRS.has(name);
  };

  const recurse = (absDir: string, relDir: string): void => {
    const entries = readEntries(absDir);
    for (const entry of entries.sort((a, b) => (a.name < b.name ? -1 : 1))) {
      const rel = relDir === "" ? entry.name : `${relDir}/${entry.name}`;
      if (entry.isDirectory()) {
        if (skipDir(entry.name)) continue;
        const childAbs = join(absDir, entry.name);
        if (childAbs === HARNESS_FIXTURES_ABS) continue;
        recurse(childAbs, rel);
      } else if (entry.isFile()) {
        if (opts.filter && !opts.filter(rel)) continue;
        out.push(rel);
      }
    }
  };

  recurse(root, "");
  return out;
}

/** Read a repo-relative file as UTF-8. Throws if absent — callers check first. */
export function read(root: string, relPath: string): string {
  return readFileSync(join(root, relPath), "utf-8");
}

/** True when a repo-relative path exists (file or directory). */
export function exists(root: string, relPath: string): boolean {
  return existsSync(join(root, relPath));
}

/** True when a repo-relative path exists and is a directory. */
export function isDir(root: string, relPath: string): boolean {
  try {
    return statSync(join(root, relPath)).isDirectory();
  } catch {
    return false;
  }
}

/** Resolve a repo-relative path to an absolute one under `root`. */
export function abs(root: string, relPath: string): string {
  return join(root, relPath);
}
