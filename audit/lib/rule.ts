// The rule contract. A rule is a pure scanner: `run(context) -> Finding[]`. It
// reads files through the context (so it runs unchanged against a fixture root)
// and returns findings. It MUST NOT mutate files, start servers, or shell out to
// anything that does. See docs/audit-harness.md → "Implementation Shape".
//
// To add a rule: write `rules/<name>.ts` exporting a `Rule`, register it in
// `rules/index.ts`, and (for a gating rule) add `fixtures/<id>/{bad,good}/`.

import type { Finding } from "./finding.ts";
import { walk, read, exists, isDir, abs, type WalkOptions } from "./repo.ts";

/**
 * When a rule runs, and therefore whether CI can fail on it:
 * - `core`     — fast, deterministic; runs on `npm run audit` (the PR gate) and
 *                in agent mode. The only tier whose findings gate CI.
 * - `heuristic`— non-blocking agent guidance (literals, css, cohesion, …); runs
 *                only in agent mode. Never fatal (the doc forbids it).
 * - `post-build`— checks that need an emitted `dist/` (link crawls, public-
 *                Markdown asset scans, archive scans); runs on
 *                `npm run audit:post-build`.
 */
export type Tier = "core" | "heuristic" | "post-build";

/** The read-only view of a tree that a rule scans. Bound to one root. */
export interface RuleContext {
  /** Absolute path to the tree under audit (repo root, or a fixture root). */
  readonly root: string;
  /** List files as repo-relative POSIX paths, skipping build/vendor junk. */
  walk(opts?: WalkOptions): string[];
  /** Read a repo-relative file as UTF-8 (throws if absent — check `exists`). */
  read(relPath: string): string;
  /** True if a repo-relative path exists. */
  exists(relPath: string): boolean;
  /** True if a repo-relative path exists and is a directory. */
  isDir(relPath: string): boolean;
  /** Resolve a repo-relative path to an absolute one under `root`. */
  abs(relPath: string): string;
}

export interface Rule {
  /** Stable id. Doubles as the fixtures/<id>/ directory name. */
  id: string;
  /** One-line human title shown in self-test output. */
  title: string;
  tier: Tier;
  run(ctx: RuleContext): Finding[] | Promise<Finding[]>;
}

/** Build the context a rule sees, rooted at `root`. Used by the runner and selftest. */
export function makeContext(root: string): RuleContext {
  return {
    root,
    walk: (opts) => walk(root, opts),
    read: (relPath) => read(root, relPath),
    exists: (relPath) => exists(root, relPath),
    isDir: (relPath) => isDir(root, relPath),
    abs: (relPath) => abs(root, relPath),
  };
}
