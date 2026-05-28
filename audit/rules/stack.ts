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

// The tsconfigs whose `include` globs DEFINE the production-source trees (the
// place TypeScript is mandated): the app config + the tooling/specs config.
const TSCONFIGS: readonly string[] = ["tsconfig.json", "tsconfig.scripts.json"];

/**
 * Derive the production-source top-level dirs from the tsconfig `include` globs
 * (`src/**\/*` → `src`, `build/**\/*.ts` → `build`, `audit/**\/*.ts` → `audit`,
 * `tests/**\/*.ts` → `tests`) rather than restating them — adding a new TS source tree to a tsconfig
 * automatically brings it under the rule, and a root tooling config (no `/` in
 * the glob, e.g. `astro.config.ts`) or anything OUTSIDE these trees (root
 * `*.config.cjs`, generated public payloads) is not production source and so is
 * not flagged. Returns null when no include globs are found, so
 * the caller can fail loud instead of scanning with no roots.
 */
function productionTsRoots(ctx: RuleContext): Set<string> | null {
  const roots = new Set<string>();
  for (const tc of TSCONFIGS) {
    if (!ctx.exists(tc)) continue;
    let parsed: unknown;
    try {
      parsed = JSON.parse(ctx.read(tc));
    } catch {
      continue;
    }
    const include = (parsed as { include?: unknown }).include;
    if (!Array.isArray(include)) continue;
    for (const glob of include) {
      if (typeof glob !== "string" || !glob.includes("/")) continue; // root files aren't a tree
      const top = glob.split("/")[0];
      if (top && !top.includes("*")) roots.add(top);
    }
  }
  return roots.size > 0 ? roots : null;
}

/**
 * Flag any handwritten-JavaScript file (`.js`/`.mjs`/`.cjs`/`.jsx`) inside a
 * production-source tree (derived from the tsconfig `include`s). One fatal finding
 * per offending file — production source is TypeScript.
 *
 * Scoping to the TS-mandated trees (not "everything except an allowlist") is what
 * keeps it false-positive-free: a legitimate root tooling config (`*.config.cjs`)
 * and generated public payloads sit OUTSIDE the tsconfig-included source roots
 * and are silently allowed. It walks the working tree
 * (not `git ls-files`) so the same rule runs against a fixture, which has no git;
 * a stray untracked `.js` in a production-source tree is still out of stack.
 */
export const pan016SourceLanguage: Rule = {
  id: SOURCE_LANG_ID,
  title: "PAN016: production source is TypeScript — no handwritten JavaScript in the TS-mandated trees",
  tier: "core",
  run(ctx: RuleContext): Finding[] {
    const roots = productionTsRoots(ctx);
    if (roots === null) {
      return [
        {
          rule: SOURCE_LANG_ID,
          severity: "fatal",
          category: CATEGORY,
          file: TSCONFIGS[0],
          observed: `no tsconfig \`include\` globs found in ${TSCONFIGS.join(" / ")} to derive the production-source trees from`,
          contract: `PAN016-source-language derives the production-source trees from the tsconfig \`include\` globs; without them it cannot tell production source from tooling/vendored files.`,
          why: `A stale premise would make the rule scan the wrong set (or nothing) and silently stop catching handwritten JS in production source.`,
          repair: `Restore the tsconfig \`include\` arrays, or update PAN016 in audit/rules/stack.ts if the config moved.`,
          doNotFixBy: `Deleting this guard.`,
        },
      ];
    }

    const offenders = ctx.walk({
      filter: (rel) =>
        JS_EXTENSIONS.some((ext) => rel.endsWith(ext)) && roots.has(rel.split("/")[0]),
    });

    return offenders.map((rel) => ({
      rule: SOURCE_LANG_ID,
      severity: "fatal",
      category: CATEGORY,
      file: rel,
      observed: `${rel} is handwritten JavaScript inside a production-source tree (${[...roots].join(", ")}); production source is TypeScript`,
      contract: `Production source is TypeScript (architecture.md Stack; tsconfig allowJs:false). The production-source trees are derived from the tsconfig \`include\` globs (${[...roots].join(", ")}); JS outside them — root tooling configs and generated public payloads — is allowed.`,
      why: `A .js file in a TS-mandated tree ships outside the strict typecheck (allowJs:false) and teaches the wrong stack to future agents who read local examples as the pattern.`,
      repair: `Rewrite it as .ts; if it is genuinely tooling config or non-production, it belongs outside the TS source trees (it is only flagged because it sits inside one).`,
      doNotFixBy: `Adding the path to a broad ignore list, or renaming .js → .ts with no real typing just to pass the extension check.`,
    }));
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
    repair: `Update PAN016 in audit/rules/stack.ts (and the architecture.md Stack "Framework" line) so the banned-framework set can be derived again.`,
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
 * The first banned token a PACKAGE NAME denotes, or null. Matches an npm package
 * name (not a substring): the name IS the token (`vue`, `react`), is token-hyphen-
 * prefixed (`react-dom`, `tailwind-merge`, `solid-js`), is the token's scope
 * (`@vue/…`), or is the framework's joined form (`tailwindcss`). Crucially it does
 * NOT fire on a name that merely CONTAINS a token mid-word — `consolidate` (solid),
 * `vuex` / `vuex-helper-typings` (vue) — which the old substring match wrongly
 * flagged as a fatal. Used for both dependency keys and (via the package extractor
 * below) import specifiers, so the two surfaces match identically.
 */
function bannedPackage(name: string, banned: ReadonlySet<string>): string | null {
  const n = name.toLowerCase();
  // A bare package name (or the name-part of a scoped one) that denotes the token:
  // `vue`, `react-dom` (token-`-`), `tailwindcss`/`reactjs` (joined form).
  const matchesName = (s: string, t: string): boolean =>
    s === t || s.startsWith(`${t}-`) || s === `${t}css` || s === `${t}js`;

  if (n.startsWith("@") && n.includes("/")) {
    // Scoped: `@scope/sub`. A framework arrives either as its OWN scope
    // (`@vue/*`, `@sveltejs/*`, `@solidjs/*`, `@tailwindcss/*` — scope is the
    // token or token+js/css) or as a vendor integration whose NAME-part is the
    // framework (`@astrojs/react`, `@astrojs/vue`). Scope must equal a token form
    // (not merely start with it) so a benign scope like `@solidarity` is safe.
    const slash = n.indexOf("/");
    const scope = n.slice(1, slash);
    const sub = n.slice(slash + 1);
    for (const t of banned) {
      if (scope === t || scope === `${t}js` || scope === `${t}css` || matchesName(sub, t)) return t;
    }
    return null;
  }

  for (const t of banned) {
    if (matchesName(n, t)) return t;
  }
  return null;
}

/**
 * The npm package an import specifier resolves to (subpath stripped): `react`,
 * `react-dom/client` → `react-dom`, `@vue/runtime-core` → `@vue/runtime-core`,
 * `svelte/store` → `svelte`, and the project alias `@/lib/x` → `@/lib` (which
 * `bannedPackage` won't match — no FP on the alias).
 */
function importPackageName(spec: string): string {
  const parts = spec.split("/");
  return spec.startsWith("@") ? parts.slice(0, 2).join("/") : parts[0];
}

/**
 * No additional UI framework. Derive the banned set from architecture.md, then
 * flag (a) any package.json dependency/devDependency KEY that is a banned package
 * (react → "react"/"react-dom", tailwind → "tailwindcss") and (b) any import in
 * src/**\/*.ts and src/**\/*.astro whose resolved package name is banned. Matching
 * is package-name-based, not substring, so `consolidate`/`vuex` don't false-fire.
 * One fatal finding per offending dep/import.
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
        const token = bannedPackage(key, banned);
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
        const token = bannedPackage(importPackageName(spec), banned);
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
