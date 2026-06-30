// PAN027 — key terms render with their canonical English (data/translation-glossary.yaml).
//
// The glossary fixes one English rendering per key Russian term. Most entries are
// `manual` (editor judgment, not machine-checked), but a few name renderings that
// are NEVER a faithful translation of anything — "Holy Russia" / "Holy Commonwealth"
// for Святая Русь (Rus is not a country), "Pankratius" for the author's name. Those
// carry `enforcement: denylist` and an `avoid` list; this rule fails on any of them
// surviving in an en.md. `enforcement: flag` is the same scan at warning severity
// (a normalization, e.g. the Conduit Mode name, not a hard error).
//
// The patterns are READ FROM THE GLOSSARY, never hardcoded here: the contract is the
// data file, and a future term added with `enforcement: denylist` is enforced with no
// code change. Matching is whole-word and case-sensitive, so the denylist catches the
// exact drift ("Pankratius") without touching the unrelated name "Pankratiy".

import { parse } from "yaml";
import type { Rule, RuleContext } from "../lib/rule.ts";
import type { Finding, Severity } from "../lib/finding.ts";

const ID = "PAN027-terminology";
const CATEGORY = "terminology";
const GLOSSARY = "data/translation-glossary.yaml";

/** A parsed YAML mapping (an object, not an array or null). */
type Dict = Record<string, unknown>;

/** One forbidden rendering, compiled from a glossary `avoid` entry. */
interface Forbidden {
  pattern: RegExp;
  phrase: string;
  canonical: string;
  ru: string;
  severity: Severity;
}

const isStr = (v: unknown): v is string => typeof v === "string" && v.length > 0;
const escape = (s: string): string => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const asDict = (v: unknown): Dict | null =>
  v !== null && typeof v === "object" && !Array.isArray(v) ? (v as Dict) : null;

/** The forbidden renderings one glossary term declares (denylist → fatal, flag → warning). */
function termForbidden(raw: unknown): Forbidden[] {
  const term = asDict(raw);
  const en = term && asDict(term.en);
  if (!term || !en) return [];
  const severity: Severity | null =
    en.enforcement === "denylist" ? "fatal" : en.enforcement === "flag" ? "warning" : null;
  if (severity === null) return []; // `manual` entries are not machine-checked
  const avoid = Array.isArray(en.avoid) ? en.avoid.filter(isStr) : [];
  const canonical = isStr(en.use) ? en.use : "";
  const ru = isStr(term.ru) ? term.ru : "";
  return avoid.map((phrase) => ({
    pattern: new RegExp(`\\b${escape(phrase)}\\b`),
    phrase,
    canonical,
    ru,
    severity,
  }));
}

/** Compile the denylist/flag patterns from the glossary, or a parse finding. */
function compile(ctx: RuleContext): Forbidden[] | Finding {
  let parsed: unknown;
  try {
    parsed = parse(ctx.read(GLOSSARY));
  } catch (err: unknown) {
    return parseFinding(`the glossary is not valid YAML: ${String(err)}`);
  }
  const root = asDict(parsed);
  if (!root || !Array.isArray(root.terms)) {
    return parseFinding("the glossary must be a mapping with a `terms` list");
  }
  return root.terms.flatMap(termForbidden);
}

export const pan027Terminology: Rule = {
  id: ID,
  title: "PAN027: key terms use their canonical English (data/translation-glossary.yaml)",
  tier: "core",
  run(ctx: RuleContext): Finding[] {
    if (!ctx.exists(GLOSSARY)) return [];
    const forbidden = compile(ctx);
    if (!Array.isArray(forbidden)) return [forbidden];
    if (forbidden.length === 0) return [];

    const findings: Finding[] = [];
    for (const rel of ctx.walk({ filter: (p) => p.endsWith("/en.md") })) {
      const lines = ctx.read(rel).split("\n");
      lines.forEach((line, i) => {
        for (const f of forbidden) {
          if (!f.pattern.test(line)) continue;
          findings.push(forbiddenFinding(rel, i + 1, f));
        }
      });
    }
    return findings;
  },
};

function forbiddenFinding(file: string, line: number, f: Forbidden): Finding {
  return {
    rule: ID,
    severity: f.severity,
    category: CATEGORY,
    file,
    line,
    observed: `${file}:${line} uses "${f.phrase}" — the canonical English for «${f.ru}» is "${f.canonical}"`,
    contract: `data/translation-glossary.yaml fixes one canonical English rendering per key Russian term; "${f.phrase}" is on the «${f.ru}» entry's avoid list (enforcement: ${f.severity === "fatal" ? "denylist" : "flag"}).`,
    why:
      f.severity === "fatal"
        ? `"${f.phrase}" is never a faithful translation of «${f.ru}» — it imports a meaning the Russian denies (e.g. Rus is not the country Russia). Leaking it splinters the library's vocabulary across pages, books, and projects.`
        : `"${f.phrase}" is a non-canonical rendering of «${f.ru}»; the corpus should name it one way ("${f.canonical}").`,
    repair: `Replace "${f.phrase}" with "${f.canonical}" in ${file}.`,
    doNotFixBy: `Adding "${f.phrase}" to the glossary's alts/avoid carve-outs or downgrading its enforcement — the committed English must use the canonical "${f.canonical}".`,
  };
}

function parseFinding(observed: string): Finding {
  return {
    rule: ID,
    severity: "fatal",
    category: CATEGORY,
    file: GLOSSARY,
    observed: `${GLOSSARY}: ${observed}`,
    contract: `PAN027 reads ${GLOSSARY} to derive the canonical-terminology denylist; it must be parseable YAML with a \`terms\` list of { ru, en: { use, avoid?, enforcement } }.`,
    why: `An unparseable glossary means the terminology guard is blind — a stale premise is worse than a loud failure.`,
    repair: `Fix the YAML at ${GLOSSARY} so it parses with the documented shape.`,
    doNotFixBy: `Deleting or emptying the glossary to silence the error instead of repairing it.`,
  };
}
