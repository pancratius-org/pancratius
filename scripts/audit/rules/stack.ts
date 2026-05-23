// Stack conformance (docs/audit-harness.md → "PAN016: Stack Conformance"). The
// architecture declares a bounded technology surface; anything outside it is
// drift even when the site still builds. These two deterministic fatal rules are
// the textbook small core members of that family:
//
//   PAN016-source-language — production source is TypeScript; no handwritten JS.
//   PAN016-ui-framework    — no additional UI framework (React/Vue/Svelte/…),
//                            neither as a dependency nor imported in source.
//
// Both DERIVE their premise (the non-prod allowlist, the banned-framework set)
// from the source of truth rather than hardcoding it (PAN003 "derive, do not
// restate"): the allowlist is the tsconfig.json `exclude` non-prod trees plus the
// doc's named vendored exception; the banned set is read out of the
// architecture.md Stack "Framework" line. Adding a forbidden framework trips the
// rule because it appears in package.json/imports, not because the rule names it.

import ts from "typescript";

import type { Rule, RuleContext } from "../lib/rule.ts";
import type { Finding } from "../lib/finding.ts";
import { parseModule } from "../lib/ast.ts";

const CATEGORY = "stack";

const ARCHITECTURE = "docs/architecture.md";
const PACKAGE_JSON = "package.json";

// PAN016-source-language ----------------------------------------------------

const SOURCE_LANG_ID = "PAN016-source-language";

// Handwritten-JavaScript extensions. `.ts`/`.astro` are the production surface;
// these are the out-of-stack ones (architecture.md Stack: "No handwritten
// production JavaScript (.js / .mjs / .cjs)"; tsconfig `allowJs: false`).
const JS_EXTENSIONS: readonly string[] = [".js", ".mjs", ".cjs", ".jsx"];

// Declared non-production / vendored trees where a `.js` is allowed. The walker
// already prunes generated/disposable trees (dist/.cache/.astro/node_modules/
// __pycache__), so the explicit allowlist is only the doc's NAMED non-prod and
// vendored exceptions:
//   - `legacy/` and `design/` — the tsconfig.json `exclude` non-prod trees
//     (archived prototypes excluded from the strict typecheck until deleted).
//   - `public/pagefind/` — the doc's vendored third-party output exception.
const NON_PROD_PREFIXES: readonly string[] = ["legacy/", "design/", "public/pagefind/"];

/**
 * Flag any tracked handwritten-JavaScript file (`.js`/`.mjs`/`.cjs`/`.jsx`)
 * outside the declared non-production / vendored trees. One fatal finding per
 * offending file — production source is TypeScript.
 */
export const pan016SourceLanguage: Rule = {
  id: SOURCE_LANG_ID,
  title: "PAN016: production source is TypeScript — no handwritten JavaScript outside declared non-prod trees",
  tier: "core",
  run(ctx: RuleContext): Finding[] {
    const findings: Finding[] = [];

    const offenders = ctx.walk({
      filter: (rel) =>
        JS_EXTENSIONS.some((ext) => rel.endsWith(ext)) &&
        !NON_PROD_PREFIXES.some((prefix) => rel.startsWith(prefix)),
    });

    for (const rel of offenders) {
      findings.push({
        rule: SOURCE_LANG_ID,
        severity: "fatal",
        category: CATEGORY,
        file: rel,
        observed: `${rel} is handwritten JavaScript; production source is TypeScript`,
        contract: `Production source is TypeScript (architecture.md Stack; tsconfig allowJs:false). Handwritten JavaScript (.js/.mjs/.cjs/.jsx) is allowed only in the declared non-production / vendored trees (${NON_PROD_PREFIXES.join(", ")}) and the generated trees the walker already prunes.`,
        why: `A .js file ships outside the strict typecheck (tsconfig excludes it / allowJs is false) and teaches the wrong stack to future agents who read local examples as the pattern to follow.`,
        repair: `Rewrite it as .ts (or, if it is genuinely non-production, move it under a declared non-prod tree such as ${NON_PROD_PREFIXES.join(", ")}).`,
        doNotFixBy: `Adding the path to a broad ignore list, or renaming .js → .ts with no real typing just to pass the extension check.`,
      });
    }

    return findings;
  },
};

// PAN016-ui-framework -------------------------------------------------------

const UI_FRAMEWORK_ID = "PAN016-ui-framework";

const UI_CONTRACT =
  "Astro + vanilla only, no other UI framework (architecture.md Stack).";
const UI_WHY =
  "A second framework changes the shipped stack and the rendering model — the site no longer compiles to plain Astro + vanilla CSS, and future agents inherit the wrong architecture.";
const UI_DO_NOT_FIX_BY =
  "Vendoring the framework under public/ to dodge the dependency scan — that ships the same out-of-stack runtime, just hidden from the manifest.";

/**
 * Banned UI-framework tokens DERIVED from the architecture.md Stack "Framework"
 * line: the line containing "No additional UI framework", from which we take the
 * proper-noun token after each "no " (lowercased). Returns null when that line —
 * or any token on it — can't be found, so the caller can fire a stale-premise
 * finding instead of scanning against a wrong (empty) banned set.
 */
function deriveBannedTokens(archMd: string): Set<string> | null {
  const line = archMd
    .split("\n")
    .find((l) => l.includes("No additional UI framework"));
  if (line === undefined) return null;

  // Each `no <ProperNoun>` after the marker → the framework name. The token must
  // start uppercase (a proper noun) so "No additional" / "no longer" don't count.
  const tokens = new Set<string>();
  const re = /\bno\s+([A-Z][A-Za-z]+)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(line)) !== null) tokens.add(m[1].toLowerCase());

  return tokens.size === 0 ? null : tokens;
}

/** The single "premise stale — update PAN016" finding for the UI-framework rule. */
function uiStalePremise(observed: string): Finding {
  return {
    rule: UI_FRAMEWORK_ID,
    severity: "fatal",
    category: CATEGORY,
    file: ARCHITECTURE,
    observed,
    contract: `PAN016-ui-framework derives its banned-framework set from the architecture.md Stack "Framework" line (the one containing "No additional UI framework"); the rule reads the names off that line rather than hardcoding them.`,
    why: `If that line (or its banned tokens) can't be found, the rule can no longer tell which frameworks are forbidden and would silently stop catching a banned dependency/import — a stale premise is worse than a loud failure.`,
    repair: `Update PAN016 in scripts/audit/rules/stack.ts (and the architecture.md Stack "Framework" line) so the banned-framework set can be derived again.`,
    doNotFixBy: `Deleting this guard — it exists precisely so the rule can't go silently stale when the SoT moves.`,
  };
}

/** All dependency KEYS across `dependencies` + `devDependencies` in a package.json. */
function dependencyKeys(packageJson: string): string[] {
  let parsed: unknown;
  try {
    parsed = JSON.parse(packageJson);
  } catch {
    return [];
  }
  if (parsed === null || typeof parsed !== "object") return [];
  const pkg = parsed as Record<string, unknown>;
  const keys: string[] = [];
  for (const field of ["dependencies", "devDependencies"] as const) {
    const block = pkg[field];
    if (block !== null && typeof block === "object") {
      keys.push(...Object.keys(block as Record<string, unknown>));
    }
  }
  return keys;
}

/** Every import/re-export module specifier in a parsed module, with its 1-based line. */
function importSpecifiers(sf: ts.SourceFile): { spec: string; line: number }[] {
  const out: { spec: string; line: number }[] = [];
  for (const stmt of sf.statements) {
    // `import … from "X"` and bare `import "X"`.
    if (ts.isImportDeclaration(stmt) && ts.isStringLiteral(stmt.moduleSpecifier)) {
      out.push({
        spec: stmt.moduleSpecifier.text,
        line: ts.getLineAndCharacterOfPosition(sf, stmt.moduleSpecifier.getStart()).line + 1,
      });
    }
    // `export … from "X"` (re-export) — also a module-graph edge.
    if (
      ts.isExportDeclaration(stmt) &&
      stmt.moduleSpecifier &&
      ts.isStringLiteral(stmt.moduleSpecifier)
    ) {
      out.push({
        spec: stmt.moduleSpecifier.text,
        line: ts.getLineAndCharacterOfPosition(sf, stmt.moduleSpecifier.getStart()).line + 1,
      });
    }
  }
  return out;
}

/**
 * The first banned token contained (as a substring) in `haystack`, or null. Used
 * for package.json dependency KEYS, where substring is correct: `react-dom` and
 * `tailwindcss` both embed their token, and you control package.json so an
 * incidental dep name is implausible.
 */
function matchedToken(haystack: string, banned: ReadonlySet<string>): string | null {
  const lower = haystack.toLowerCase();
  for (const token of banned) {
    if (lower.includes(token)) return token;
  }
  return null;
}

/**
 * The first banned token matched in an IMPORT specifier by PATH SEGMENT, or null.
 * Module specifiers are "/"-delimited and may be scoped (`@vue/runtime-core`), so
 * we match a segment that IS the token, is token-prefixed (`react-dom`), or is the
 * scope (`@vue/…`). Substring matching would false-positive a fatal on a local
 * path that incidentally embeds a token (`@/lib/reactor`, `@/lib/revue`); segment
 * matching catches every real framework module path without that risk.
 */
function bannedImportToken(spec: string, banned: ReadonlySet<string>): string | null {
  const segments = spec.split("/").map((s) => (s.startsWith("@") ? s.slice(1) : s).toLowerCase());
  for (const seg of segments) {
    for (const token of banned) {
      if (seg === token || seg.startsWith(`${token}-`)) return token;
    }
  }
  return null;
}

/**
 * No additional UI framework. Derive the banned set from architecture.md, then
 * flag (a) any package.json dependency/devDependency KEY containing a banned
 * token as a substring (react → "react"/"react-dom", tailwind → "tailwindcss")
 * and (b) any import specifier in src/**\/*.ts and src/**\/*.astro whose module
 * path contains a banned token. One fatal finding per offending dep/import.
 */
export const pan016UiFramework: Rule = {
  id: UI_FRAMEWORK_ID,
  title: "PAN016: no additional UI framework (React/Vue/Svelte/…) as a dependency or import",
  tier: "core",
  run(ctx: RuleContext): Finding[] {
    const findings: Finding[] = [];

    // Derive the banned set from the SoT; stale-premise if it can't be read.
    const banned = ctx.exists(ARCHITECTURE)
      ? deriveBannedTokens(ctx.read(ARCHITECTURE))
      : null;
    if (banned === null) {
      findings.push(
        uiStalePremise(
          `${ARCHITECTURE} has no Stack "Framework" line containing "No additional UI framework" with banned-framework tokens to derive`,
        ),
      );
      return findings;
    }

    // (a) package.json dependency/devDependency keys.
    if (ctx.exists(PACKAGE_JSON)) {
      for (const key of dependencyKeys(ctx.read(PACKAGE_JSON))) {
        const token = matchedToken(key, banned);
        if (token === null) continue;
        findings.push({
          rule: UI_FRAMEWORK_ID,
          severity: "fatal",
          category: CATEGORY,
          file: PACKAGE_JSON,
          observed: `${PACKAGE_JSON} declares dependency "${key}" — a banned UI framework ("${token}")`,
          contract: UI_CONTRACT,
          why: UI_WHY,
          repair: `Remove the "${key}" dependency from ${PACKAGE_JSON}.`,
          doNotFixBy: UI_DO_NOT_FIX_BY,
        });
      }
    }

    // (b) import specifiers in src TypeScript / Astro modules.
    const srcModules = ctx.walk({
      filter: (rel) =>
        rel.startsWith("src/") && (rel.endsWith(".ts") || rel.endsWith(".astro")),
    });
    for (const rel of srcModules) {
      const sf = parseModule(rel, ctx.read(rel));
      if (!sf) continue;
      for (const { spec, line } of importSpecifiers(sf)) {
        const token = bannedImportToken(spec, banned);
        if (token === null) continue;
        findings.push({
          rule: UI_FRAMEWORK_ID,
          severity: "fatal",
          category: CATEGORY,
          file: rel,
          line,
          observed: `${rel} imports "${spec}" — a banned UI framework ("${token}")`,
          contract: UI_CONTRACT,
          why: UI_WHY,
          repair: `Remove the import of "${spec}".`,
          doNotFixBy: UI_DO_NOT_FIX_BY,
        });
      }
    }

    return findings;
  },
};
