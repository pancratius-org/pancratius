// Built-surface internal-link crawl (docs/audit-harness.md → "PAN014:
// Built-Surface Crawl And Index Sanity"). A `deploy`-tier rule: it crawls an
// emitted `dist/`, so it runs only on `npm run audit:deploy` (post-build), never
// on the fast PR gate.
//
// `astro build` alone does not prove the deployed site is internally consistent:
// only crawling the EMITTED files as a plain static host would serve them shows
// whether every internal link resolves to a file that was actually written. A
// static file host returns 404 for a link to a non-emitted page or asset, so a
// broken internal link is a real production defect that nothing before this gate
// catches.
//
// SCOPE (deliberate, documented so a future agent knows the boundary):
//   - SINGLE primary target: this build's base path is "/" (the canonical
//     deploy), so absolute links are resolved as `dist` + path. The mirror /
//     base-path target (whose `base` differs) is a NOTED FOLLOW-UP — its links
//     resolve against `dist/<base>/…` and need a second pass keyed off that
//     target's config.
//   - LINK/ASSET EXISTENCE only: href/src (incl. `<link href>` and
//     `<script src>`). It does NOT verify sitemap/feed/hreflang/canonical URL
//     parity, Pagefind index presence/loadability, or search-surface coverage —
//     all NOTED FOLLOW-UPS in docs/audit-harness.md PAN014.
//   - `srcset` (responsive image candidate lists) is NOT parsed; only single
//     `src`/`href` values are. The `<img src>` fallback covers the common case;
//     full srcset coverage is a follow-up.
//   - The BODY of inline `<script>`/`<style>` is masked before extraction: it is
//     JavaScript/CSS, not crawlable HTML, and the emitted search/graph scripts
//     build markup with template literals (e.g. `<a href="${escape(h.url)}">`)
//     whose URLs are resolved at RUNTIME, not static links. The opening tag's
//     real `src` (`<script src="/_astro/x.js">`) is still checked.
//
// PRECISION: a fatal false-positive on the real dist would block every deploy,
// so the resolver mirrors a static host's behavior exactly — trailing-slash dir
// links map to `index.html`, extensionless links fall back to
// `index.html`/`.html`/raw, paths are treated case-sensitively, and
// external/non-file schemes plus pure anchors are skipped before any resolution.

import { dirname, normalize, posix } from "node:path";

import type { Rule, RuleContext } from "../lib/rule.ts";
import type { Finding } from "../lib/finding.ts";

const ID = "PAN014-internal-links";
const CATEGORY = "built-surface";

// Cap the number of findings emitted in the report (we still COUNT every broken
// link so the summary line is honest about scale).
const MAX_FINDINGS = 100;

const CONTRACT =
  "Every internal link/asset reference in emitted HTML (href/src, including <link> and <script>) must point to a file that exists in dist, with the target's base path applied (this build's base is \"/\").";
const WHY =
  "A static file host returns 404 for a link to a non-emitted page or file — `astro build` succeeding proves nothing about cross-page link integrity. This crawl of the emitted files is the only thing that proves the built site is internally consistent before it ships.";
const REPAIR =
  "Fix the link to the correct emitted path, or ensure the target route/asset is actually emitted into dist (add the page/asset, or correct the route that should generate it).";
const DO_NOT_FIX_BY =
  "Adding a redirect hack or relying on the dev server's route fallback — production is a plain file host with no fallback, so the link must resolve to a real emitted file.";

/** Schemes / forms that are not local files and must be skipped before resolving. */
function isSkippable(url: string): boolean {
  if (url === "") return true;
  if (url.startsWith("#")) return true; // pure in-page anchor
  if (url.startsWith("//")) return true; // protocol-relative external
  // Any explicit scheme we treat as off-site / non-file.
  return /^(?:https?|mailto|tel|data|javascript):/i.test(url);
}

/** Strip a #fragment and ?query, returning just the path portion. */
function stripFragmentQuery(url: string): string {
  let path = url;
  const hash = path.indexOf("#");
  if (hash !== -1) path = path.slice(0, hash);
  const q = path.indexOf("?");
  if (q !== -1) path = path.slice(0, q);
  return path;
}

/**
 * Resolve a stripped link to a list of candidate dist-relative POSIX paths to
 * test, in priority order. The link is considered resolved if ANY candidate
 * exists. `htmlRelDir` is the dist-relative POSIX directory of the HTML file the
 * link was found in (e.g. `dist/books/foo`).
 */
function candidatesFor(linkPath: string, htmlRelDir: string): string[] {
  // Decode percent-encoding so `/a%20b/` matches an `a b` file on disk; leave
  // the raw path if it is not valid encoding.
  let decoded = linkPath;
  try {
    decoded = decodeURIComponent(linkPath);
  } catch {
    decoded = linkPath;
  }

  const isAbsolute = decoded.startsWith("/");
  const endsWithSlash = decoded.endsWith("/");

  // Build the base dist-relative path (POSIX), without normalizing away the
  // trailing slash (we need it to choose the dir-vs-file branch).
  let base: string;
  if (isAbsolute) {
    // base "/" → absolute path is rooted at dist.
    base = posix.join("dist", decoded);
  } else {
    // Relative (./a, ../a, a) → resolve against the HTML file's directory.
    base = posix.join(htmlRelDir, decoded);
  }
  // posix.join collapses `.`/`..` and drops a trailing slash; re-derive the
  // trailing-slash intent from the original link, and guard against escaping
  // above dist (a `..`-walk that climbs out is, by construction, missing).
  if (base !== "dist" && !base.startsWith("dist/")) {
    // Escaped the dist root — no candidate can exist.
    return [];
  }

  const lastSeg = base.slice(base.lastIndexOf("/") + 1);
  const hasExtension = lastSeg.includes(".") && !endsWithSlash;

  if (endsWithSlash) {
    // Trailing-slash path → its index.html.
    return [posix.join(base, "index.html")];
  }
  if (hasExtension) {
    // A concrete file reference (e.g. /favicon.svg, /_astro/x.css).
    return [base];
  }
  // Bare extensionless path with no trailing slash → host tries directory index
  // then a sibling .html then the raw file.
  return [posix.join(base, "index.html"), `${base}.html`, base];
}

/**
 * Mask the BODY of every <script> and <style> element with spaces (keeping
 * newlines so line numbers stay correct), leaving the opening/closing tags
 * intact. Inline <script> bodies are JavaScript, not crawlable HTML — emitted
 * Astro search/graph scripts build markup with template literals like
 * `<a href="${escape(h.url)}">`, which is a runtime-resolved link, not a static
 * one. Masking the body keeps those out of extraction while preserving the
 * opening tag's real `src` attribute (`<script src="/_astro/x.js">`).
 */
function maskScriptStyleBodies(html: string): string {
  return html.replace(
    /(<(script|style)\b[^>]*>)([\s\S]*?)(<\/\2\s*>)/gi,
    (_full, open: string, _tag: string, body: string, close: string) => {
      const masked = body.replace(/[^\n]/g, " ");
      return open + masked + close;
    },
  );
}

/** Extract href/src attribute values from emitted HTML (regex; emitted Astro HTML is regular). */
function extractLinks(html: string): { url: string; line: number }[] {
  const scanned = maskScriptStyleBodies(html);
  const out: { url: string; line: number }[] = [];
  const re = /(?:href|src)\s*=\s*"([^"]*)"/gi;
  let m: RegExpExecArray | null;
  while ((m = re.exec(scanned)) !== null) {
    // 1-based line of the match start (cheap single pass per match). Offsets are
    // preserved by the mask (same length, newlines kept), so the line is exact.
    let line = 1;
    for (let i = 0; i < m.index; i++) if (scanned.charCodeAt(i) === 10) line += 1;
    out.push({ url: m[1], line });
  }
  return out;
}

/**
 * PAN014: every internal link/asset reference in emitted HTML must resolve to a
 * file that exists in dist (base "/"). One fatal finding per broken (file, link)
 * pair, deduped; findings capped, count reported.
 */
export const pan014InternalLinks: Rule = {
  id: ID,
  title: "PAN014: every internal link/asset reference in emitted dist/ HTML must resolve to a file that exists",
  tier: "deploy",
  run(ctx: RuleContext): Finding[] {
    const htmlFiles = ctx.walk({
      unignore: ["dist"],
      filter: (rel) => rel.startsWith("dist/") && rel.endsWith(".html"),
    });

    // Existence cache over the dist tree so repeated link targets are O(1).
    const existsInDist = new Map<string, boolean>();
    const distHas = (relPath: string): boolean => {
      const norm = normalize(relPath).split("\\").join("/");
      const cached = existsInDist.get(norm);
      if (cached !== undefined) return cached;
      const ok = ctx.exists(norm) && !ctx.isDir(norm);
      existsInDist.set(norm, ok);
      return ok;
    };

    const findings: Finding[] = [];
    const seen = new Set<string>(); // dedupe identical (htmlfile, link) pairs
    let brokenCount = 0;

    for (const htmlRel of htmlFiles) {
      const html = ctx.read(htmlRel);
      const htmlRelDir = dirname(htmlRel).split("\\").join("/");

      for (const { url, line } of extractLinks(html)) {
        if (isSkippable(url)) continue;
        const linkPath = stripFragmentQuery(url);
        if (linkPath === "" || isSkippable(linkPath)) continue;

        const candidates = candidatesFor(linkPath, htmlRelDir);
        if (candidates.length === 0) {
          // Resolved out of the dist root — unresolvable by construction; fall
          // through to the broken-link branch below.
        } else if (candidates.some((c) => distHas(c))) {
          continue; // resolves
        }

        const key = `${htmlRel} ${url}`;
        if (seen.has(key)) continue;
        seen.add(key);
        brokenCount += 1;

        if (findings.length >= MAX_FINDINGS) continue;
        findings.push({
          rule: ID,
          severity: "fatal",
          category: CATEGORY,
          file: htmlRel,
          line,
          observed: `${htmlRel}:${line} links to \`${url}\` — no matching file emitted in dist/ (tried: ${candidates.length ? candidates.join(", ") : "<escaped dist root>"})`,
          contract: CONTRACT,
          why: WHY,
          repair: REPAIR,
          doNotFixBy: DO_NOT_FIX_BY,
        });
      }
    }

    if (brokenCount > findings.length && findings.length > 0) {
      // Surface the true scale even though the report is capped.
      findings.push({
        rule: ID,
        severity: "fatal",
        category: CATEGORY,
        file: "dist",
        observed: `${brokenCount} broken internal links found across emitted HTML; ${findings.length} shown (cap ${MAX_FINDINGS}).`,
        contract: CONTRACT,
        why: WHY,
        repair: REPAIR,
        doNotFixBy: DO_NOT_FIX_BY,
      });
    }

    return findings;
  },
};
