// Locale-route contracts (docs/audit-harness.md → PAN002, and later PAN003).
//
// PAN002 — the display-fallback selector must not gate route EXISTENCE.
// `src/lib/works.ts` exports `entryForLocale`, whose own docstring says it is
// DISPLAY-fallback only and must NOT decide whether a route or download exists.
// A route's `getStaticPaths` decides which localized routes exist via its
// returned `params` set; the fallback returns the default-locale entry, so
// letting it influence `params` (or a `.filter` membership test) emits an
// `/en/…` route for a work with no EN entry — default-locale body under a
// localized URL.
//
// Precision (docs/audit-harness.md → "Fatal rules must be deterministic":
// check the OBSERVABLE CONSEQUENCE, not mere presence): the violation is not
// "entryForLocale appears in getStaticPaths", it is "its value reaches the
// existence-deciding output". So inside a getStaticPaths body we fire on an
// `entryForLocale` call C iff ANY of:
//   (a) C is lexically within a `params:` value subtree (params ARE the
//       existence/identity output of getStaticPaths); OR
//   (b) C is the initializer (or within it) of a `const`/`let` binding `X = …`,
//       and a reference to `X` within the same body is within a `params:` value
//       subtree (the selector's value flows into params via the binding); OR
//   (c) C is within a `.filter(…)` argument subtree (a filter predicate decides
//       which elements survive → membership → existence).
// We deliberately do NOT fire when the value flows only into a `props:` value
// (that's DISPLAY data handed to the component — the legitimate use), into
// `.sort(…)`, or is otherwise unused by params/filter.

import ts from "typescript";

import type { Rule, RuleContext } from "../lib/rule.ts";
import type { Finding } from "../lib/finding.ts";
import { runPythonCheck } from "../lib/python.ts";
import {
  parseModule,
  findExportedFunctionNames,
  getStaticPathsInitializer,
  findIdentifierCallsAny,
  findLocalNamesForImport,
  findPropertyValues,
  findIdentifierRefs,
  findMethodCallArguments,
  nodeContains,
  enclosingBindingName,
  nameDeclarationCount,
} from "../lib/ast.ts";

const ID = "PAN002-fallback-existence";
const CATEGORY = "locale-route-existence";

// The display-fallback selector and the module that owns it. We confirm this
// name is actually an exported function in works.ts (the SoT) before scanning,
// so a rename fails the rule loud instead of letting it silently pass.
const SELECTOR = "entryForLocale";
const SELECTOR_SOURCE = "src/lib/works.ts";

// KNOWN GAPS (best-effort, documented so a future agent knows the boundary).
// All are realized only AFTER build as phantom localized routes, which the
// post-build crawl PAN014 (built-surface link/sitemap/hreflang sanity) catches:
//  - Namespace import then member call: `import * as w from "@/lib/works"` →
//    `w.entryForLocale(…)` (member-access callee, not a bare identifier).
//  - Indirection through a local helper: `const pick = (p) => entryForLocale(…)`
//    then `pick(p)` feeding params (we don't trace user-defined helpers).
//  - Branch (b) follows a binding by NAME, so it only fires when that name is
//    declared ONCE in the body (the `nameDeclarationCount === 1` guard). This
//    avoids a shadowing FALSE POSITIVE — an inner `.map((entry) => …params:
//    entry…)` reusing the binding's name is a different binding — at the cost of
//    missing the rare case where a re-used name genuinely also reaches params.
//  - Chained capture (`const b = a; …params: b…`) and destructuring
//    (`const { data } = entryForLocale(…)`) are not traced.
//  - Only `.filter` membership is modelled, not `.find`/`.some`/`.every`.

/** 1-based line of a node within its source file. */
function lineOf(sf: ts.SourceFile, node: ts.Node): number {
  return ts.getLineAndCharacterOfPosition(sf, node.getStart()).line + 1;
}

export const rule: Rule = {
  id: ID,
  title: "PAN002: entryForLocale (display-fallback) must not influence route params/existence in getStaticPaths",
  tier: "core",
  run(ctx: RuleContext): Finding[] {
    const findings: Finding[] = [];

    // Derive-don't-restate: tie the rule to the source of truth. If the selector
    // is no longer an exported function in works.ts, our premise is stale —
    // fail loud rather than scan pages against a wrong assumption.
    const worksSf = ctx.exists(SELECTOR_SOURCE)
      ? parseModule(SELECTOR_SOURCE, ctx.read(SELECTOR_SOURCE))
      : null;
    const exported = worksSf ? findExportedFunctionNames(worksSf) : new Set<string>();
    if (!exported.has(SELECTOR)) {
      findings.push({
        rule: ID,
        severity: "fatal",
        category: CATEGORY,
        file: SELECTOR_SOURCE,
        observed: `${SELECTOR_SOURCE} no longer exports a function named ${SELECTOR}`,
        contract: `PAN002 is anchored to the display-fallback selector exported from ${SELECTOR_SOURCE}; that exported function is the symbol the rule forbids from influencing route params inside getStaticPaths.`,
        why: `The selector was renamed or removed, so the rule can no longer recognize it and would silently stop catching phantom localized routes — a stale premise is worse than a loud failure.`,
        repair: `Update PAN002 in scripts/audit/rules/locales.ts to the selector's new name (and docs/audit-harness.md PAN002 / its docstring in ${SELECTOR_SOURCE}).`,
        doNotFixBy: `Deleting this guard — it exists precisely so the rule can't go silently stale when the SoT moves.`,
      });
      return findings;
    }

    // Scan every page-route module. `getStaticPaths` is what decides existence;
    // a call to the display-fallback selector that reaches its params/filter is
    // the violation.
    const pages = ctx.walk({
      filter: (rel) =>
        rel.startsWith("src/pages/") && (rel.endsWith(".astro") || rel.endsWith(".ts")),
    });

    for (const rel of pages) {
      const sf = parseModule(rel, ctx.read(rel));
      if (!sf) continue;
      const init = getStaticPathsInitializer(sf);
      if (!init) continue;

      // Recognize the selector under any local alias bound by a named import
      // whose original name is `entryForLocale` (closes the aliased-import
      // evasion). The literal name is always included so an un-aliased call
      // still matches even if no import specifier is found (e.g. odd casing).
      const selectorNames = findLocalNamesForImport(sf, SELECTOR);
      selectorNames.add(SELECTOR);

      // The existence-deciding output: every `params:` value subtree, and every
      // `.filter(…)` argument subtree. (`props:` and `.sort(…)` are NOT here.)
      const paramsValues = findPropertyValues(init, "params");
      const filterArgs = findMethodCallArguments(init, "filter");

      const within = (node: ts.Node, regions: ts.Node[]): boolean =>
        regions.some((r) => nodeContains(r, node));

      for (const call of findIdentifierCallsAny(init, selectorNames)) {
        // (a) the call itself sits inside a params value subtree, or
        // (c) inside a .filter(...) argument subtree.
        let influencesExistence = within(call, paramsValues) || within(call, filterArgs);

        // (b) the call is captured into `const X = entryForLocale(…)` and some
        // reference to X within this getStaticPaths body lands in a params value.
        // Only when X is declared ONCE in the body — a name-based ref scan can't
        // distinguish a shadowed re-use (idiomatic `.map((entry) => …)`), so we
        // refuse the ambiguous case rather than risk a fatal false positive.
        if (!influencesExistence) {
          const binding = enclosingBindingName(call);
          if (binding && nameDeclarationCount(init, binding) === 1) {
            const refs = findIdentifierRefs(init, binding);
            influencesExistence = refs.some((ref) => within(ref, paramsValues));
          }
        }

        if (!influencesExistence) continue;

        findings.push({
          rule: ID,
          severity: "fatal",
          category: CATEGORY,
          file: rel,
          line: lineOf(sf, call),
          observed: `getStaticPaths lets ${SELECTOR}(...) influence the returned route params (or a .filter membership test) — the display-fallback selector decides route existence here`,
          contract: `The \`params\` set returned by getStaticPaths is a route's existence/identity output, and \`.filter\` decides membership; both must read \`pair.entries[locale]\` directly. ${SELECTOR} is display-fallback only (per its docstring in ${SELECTOR_SOURCE} and docs/audit-harness.md PAN002), legitimate for \`props\`/display values.`,
          why: `The fallback returns the default-locale entry, so feeding it into params/filter emits an \`/en/…\` route for a work with no authored EN entry, rendering default-locale body under a localized URL — a route that lies about its language.`,
          repair: `Gate existence on \`pair.entries[locale]\` (filter to authored entries) and take params from that entry; if you need display data, pass it through \`props\` and let ${SELECTOR} resolve it there (or in the component).`,
          doNotFixBy: `Routing the ${SELECTOR} result into params through an intermediate variable or wrapper — the fallback still emits phantom localized routes; only DISPLAY (props) use is allowed.`,
        });
      }
    }

    return findings;
  },
};

// PAN003 — cross-language single-source-of-truth parity (docs/audit-harness.md →
// "PAN003: Single Sources Of Truth"). Two registries — the locale list+default,
// and the work-kind → URL-segment map — are intentionally written once PER
// LANGUAGE: in TypeScript (src/lib/*.ts, the source for routes/config) and in
// Python (scripts/lib/*.py, the source for build scripts), because neither
// language can import the other. These two thin rules wrap the existing,
// production-proven Python checks (scripts/audit/python/*.py): each reads BOTH
// sources and compares them (derive-don't-restate — the TS rule restates no
// codes), so the Python check is the only thing keeping the two copies in
// agreement. The check's non-zero exit (with both sides printed) becomes the
// finding via runPythonCheck.

/** PAN003: the locale list + default locale must agree between TS and Python. */
export const pan003Locales: Rule = {
  id: "PAN003-locale-parity",
  title: "PAN003: the locale list and default locale must agree between src/lib/locales.ts and scripts/lib/locales.py",
  tier: "core",
  run(ctx: RuleContext): Finding[] {
    return runPythonCheck(ctx, {
      id: "PAN003-locale-parity",
      category: "ssot-parity",
      severity: "fatal",
      script: "python/locales.py",
      contract:
        "The locale list and default locale are intentionally defined once per language — src/lib/locales.ts for routes/config, scripts/lib/locales.py for build scripts — because neither language can import the other; this audit is the only thing keeping the two copies in agreement (derive-don't-restate: it reads BOTH sources and compares, order included).",
      why: "If the two copies drift, routes/config and the build scripts disagree about which locales exist and which is default → broken URLs, wrong route emission, and mismatched build output between the site and the scripts that feed it.",
      repair:
        "Make the two source files agree — the audit output shows both sides (TS vs Python). Edit whichever side is wrong so src/lib/locales.ts and scripts/lib/locales.py declare the same locale list and default.",
      doNotFixBy:
        "Editing only one side, or loosening the audit (e.g. ignoring order or making the comparison fuzzy) to silence the mismatch instead of reconciling the two sources.",
    });
  },
};

/** PAN003: the work-kind → URL-segment map must agree between TS and Python. */
export const pan003Kinds: Rule = {
  id: "PAN003-kind-segment-parity",
  title: "PAN003: the work-kind → URL-segment map must agree between src/lib/kinds.ts and scripts/lib/kinds.py",
  tier: "core",
  run(ctx: RuleContext): Finding[] {
    return runPythonCheck(ctx, {
      id: "PAN003-kind-segment-parity",
      category: "ssot-parity",
      severity: "fatal",
      script: "python/kind_segments.py",
      contract:
        "The work-kind → URL-segment map is intentionally defined once per language — src/lib/kinds.ts for routes/config, scripts/lib/kinds.py for build scripts — because neither language can import the other; this audit is the only thing keeping the two copies in agreement (derive-don't-restate: it reads BOTH sources and compares).",
      why: "If the two copies drift, routes/config and the build scripts disagree about which URL segment a kind lives under → broken URLs, wrong route emission, and mismatched build output between the site and the scripts that feed it.",
      repair:
        "Make the two source files agree — the audit output shows both sides (TS vs Python). Edit whichever side is wrong so src/lib/kinds.ts and scripts/lib/kinds.py declare the same kind → segment map.",
      doNotFixBy:
        "Editing only one side, or loosening the audit to silence the mismatch instead of reconciling the two sources.",
    });
  },
};
