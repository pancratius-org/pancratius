// Path boundary (docs/audit-harness.md → "PAN001: Path Boundary"). Authored
// content may reference project-relative source paths, emitted public URLs, and
// external https URLs — but never a machine-local path, a parent-escape, or a
// retired `legacy/` tree. Such a reference works on the author's machine and
// breaks in a mirror, a clean clone, or CI.
//
// This is a content scan (src/content/**/*.md), so PRECISION IS PARAMOUNT: a
// false-positive fatal blocks every PR, and prose/dialogue is adversarial. The
// patterns below are deliberately narrow — each requires a REAL path character
// after the marker, so they fire on actual paths and stay silent on the prose
// near-misses that look path-ish:
//   - dialogue notation `*\[The Guide … says softly:\]*` contains the substring
//     `:\]`, which a naive Windows-drive regex `[A-Za-z]:\\` would wrongly hit;
//     ours requires `\b<drive>:\\<path-char>` and `]` is not a path char, so it
//     does NOT match (covered by the good fixture, the FP regression).
//   - the bare English word "legacy" (no following slash) is not a path ref;
//     the pattern requires `legacy/` + a path char.
//   - a relative `./images/x.jpg`, an external `https://…`, and a bare `C:` with
//     no backslash are all allowed and stay silent.

import type { Rule, RuleContext } from "../lib/rule.ts";
import type { Finding } from "../lib/finding.ts";
import { matchesWithLines, lineTextAt, snippet } from "../lib/text.ts";

const ID = "PAN001-path-boundary";
const CATEGORY = "path-boundary";

// Cap the offending-line snippet length in a finding's `observed` (long content
// lines are common in the corpus).
const SNIPPET_MAX = 120;

interface PathPattern {
  /** Human label for the disallowed path class (used only in comments/debug). */
  readonly label: string;
  /** Global regex; must require a REAL path character so prose can't trip it. */
  readonly re: RegExp;
}

// PRECISE patterns — each anchored on a real path character so prose/dialogue
// can't false-positive (validated against all of src/content: zero matches).
const PATTERNS: readonly PathPattern[] = [
  // Machine-local absolute home on macOS.
  { label: "/Users/", re: /\/Users\//g },
  // A real /home/<user>/ dir (Linux home), not the bare word "home".
  { label: "/home/<user>/", re: /\/home\/[A-Za-z0-9._-]+\//g },
  // Tilde home + a real path segment char (so a stray "~" in prose is ignored).
  { label: "~/<path>", re: /~\/[A-Za-z0-9._-]/g },
  // Windows drive: drive letter + ":" + backslash + a REAL path char. `]` is NOT
  // in the class, so the dialogue trap `softly:\]` does not match (tested).
  { label: "<drive>:\\<path>", re: /\b[A-Za-z]:\\[A-Za-z0-9._-]/g },
  // Retired-source `legacy/` used AS A PATH (followed by a path char), e.g.
  // `](legacy/foo.png)` or `"legacy/x"` — NOT the bare English word "legacy".
  { label: "legacy/<path>", re: /legacy\/[A-Za-z0-9._/-]/g },
];

const CONTRACT =
  "Production source may reference project-relative source paths (src/, data/, public/, .cache/), emitted public URLs (/assets/…), or external https URLs — but never machine-local paths (/Users/…, ~/…, C:\\…), parent-escapes, or retired `legacy/` trees (docs/audit-harness.md PAN001).";
const WHY =
  "A mirror, a clean clone, or a CI build can't depend on a machine-local or retired path — the reference resolves only on the author's machine and breaks everywhere else.";
const REPAIR =
  "Move the asset into the work bundle (co-located with the content) or into public/, and reference it by a relative path or an approved public URL.";
const DO_NOT_FIX_BY =
  "Teaching a renderer to rewrite the bad path at build time — that hides the broken dependency instead of removing it.";

/**
 * Scan authored content (src/content/**\/*.md) for embedded out-of-project or
 * retired-source paths. One fatal finding per match, located at file:line.
 */
export const pan001PathBoundary: Rule = {
  id: ID,
  title: "PAN001: authored content must not embed machine-local or retired `legacy/` paths",
  tier: "core",
  run(ctx: RuleContext): Finding[] {
    const findings: Finding[] = [];

    const mdFiles = ctx.walk({
      filter: (rel) => rel.startsWith("src/content/") && rel.endsWith(".md"),
    });

    for (const rel of mdFiles) {
      const text = ctx.read(rel);
      for (const { re } of PATTERNS) {
        for (const { match, line } of matchesWithLines(text, re)) {
          const offending = snippet(lineTextAt(text, match.index), SNIPPET_MAX);
          findings.push({
            rule: ID,
            severity: "fatal",
            category: CATEGORY,
            file: rel,
            line,
            observed: `${rel}:${line} embeds the out-of-project / retired path \`${match[0]}\` — "${offending}"`,
            contract: CONTRACT,
            why: WHY,
            repair: REPAIR,
            doNotFixBy: DO_NOT_FIX_BY,
          });
        }
      }
    }

    return findings;
  },
};
