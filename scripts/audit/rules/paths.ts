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

import { posix } from "node:path";

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

// RAW-TEXT patterns — absolute machine-local paths that are unambiguous in ANY
// context (capitalised / slashed forms that don't occur in this corpus's prose),
// validated against all of src/content: zero matches. Note what is deliberately
// NOT here:
//   - `~/` is handled in the TARGET loop only: `~/` in prose ("approximately", a
//     stray tilde) is common, so it is a path ONLY as a link/image target.
//   - `legacy/` literal is GONE: a relative `legacy/` from a content file is a
//     local sub-folder (legit, e.g. `./images/legacy/`), and `legacy/` inside an
//     external URL is legit too. A reference to the RETIRED repo-root `legacy/`
//     tree is reached via parent traversal (the escape check below catches it) or
//     an absolute `/legacy/…` URL (caught post-build by PAN014). So the literal
//     would only false-positive.
const PATTERNS: readonly PathPattern[] = [
  // Machine-local absolute home on macOS.
  { label: "/Users/", re: /\/Users\//g },
  // A real /home/<user>/ dir (Linux home), not the bare word "home".
  { label: "/home/<user>/", re: /\/home\/[A-Za-z0-9._-]+\//g },
  // Windows drive: drive letter + ":" + backslash + a REAL path char. `]` is NOT
  // in the class, so the dialogue trap `softly:\]` does not match (tested).
  { label: "<drive>:\\<path>", re: /\b[A-Za-z]:\\[A-Za-z0-9._-]/g },
];

// Markdown link/image target capture: `](url …)` → url. Used for the
// target-context checks (parent-traversal escape, `~/` home) which need to RESOLVE
// or inspect the target itself, not just match raw text.
const MD_TARGET_RE = /\]\(\s*<?([^)\s>]+)>?/g;

const CONTRACT =
  "Production source may reference project-relative source paths (src/, data/, public/, .cache/), emitted public URLs (/assets/…), or external https URLs — but never machine-local paths (/Users/…, ~/…, C:\\…) or parent-escapes out of the content tree (docs/audit-harness.md PAN001).";
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
  title: "PAN001: authored content must not embed machine-local paths or parent-traversal escapes",
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

      // Parent-traversal escape: a relative link/image target that resolves
      // OUTSIDE src/content/ depends on a path beyond the work bundle / content
      // root (the doc's `../../MyWorks` class). Resolved, not pattern-matched, so
      // an in-bundle `../sibling/x` stays silent while an escaping `../../../x`
      // fires. Skips absolute (`/…`, handled above), anchors, and scheme URLs.
      const dir = posix.dirname(rel);
      for (const { match, line } of matchesWithLines(text, MD_TARGET_RE)) {
        const raw = match[1].replace(/^<|>$/g, "");

        // A `~/…` link/image target is a machine home path (checked in target
        // context, not raw text, so a `~` in prose isn't a false positive).
        if (raw.startsWith("~/")) {
          findings.push({
            rule: ID,
            severity: "fatal",
            category: CATEGORY,
            file: rel,
            line,
            observed: `${rel}:${line} link/image target \`${raw}\` is a machine-local home path`,
            contract: CONTRACT,
            why: WHY,
            repair: REPAIR,
            doNotFixBy: DO_NOT_FIX_BY,
          });
          continue;
        }

        // Parent-traversal escape: a relative target that resolves OUTSIDE
        // src/content/ depends on a path beyond the work bundle / content root
        // (the doc's `../../MyWorks` class). Resolved, not pattern-matched, so an
        // in-bundle `../sibling/x` stays silent while an escaping `../../../x`
        // fires. Skips absolute (`/…`, handled by the raw patterns), anchors, and
        // scheme URLs (so `legacy/` inside an https URL never trips this).
        if (raw.startsWith("/") || raw.startsWith("#") || /^[a-z][a-z0-9+.-]*:/i.test(raw)) continue;
        if (!raw.includes("../")) continue;
        const resolved = posix.normalize(posix.join(dir, raw));
        if (resolved.startsWith("src/content/")) continue; // stays inside the content root
        findings.push({
          rule: ID,
          severity: "fatal",
          category: CATEGORY,
          file: rel,
          line,
          observed: `${rel}:${line} link/image target \`${raw}\` resolves to \`${resolved}\`, escaping src/content/ via parent traversal`,
          contract: CONTRACT,
          why: WHY,
          repair: REPAIR,
          doNotFixBy: DO_NOT_FIX_BY,
        });
      }
    }

    return findings;
  },
};
