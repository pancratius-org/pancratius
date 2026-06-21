// PAN023 — domain type-shape heuristics.
//
// This is deliberately not a blanket ban on `string`, tuple returns, optional
// fields, or DTO-shaped data. Those are legitimate at parsers and external
// boundaries. The TS half is intentionally narrow: exported src/lib and build
// APIs, plus exported registries, where agents most often copy public patterns.
// The Python half does the broader tuple/optionality scan.

import ts from "typescript";

import type { Finding } from "../lib/finding.ts";
import type { Rule, RuleContext } from "../lib/rule.ts";
import { parseModule } from "../lib/ast.ts";
import { runPythonCheck } from "../lib/python.ts";

const CATEGORY = "domain-type-shape";
const TS_ID = "PAN023-type-domain-ts";
const PY_ID = "PAN023-type-domain-py";
const BASELINE_PATH = "data/type-domain-baseline.json";
const SUPPRESSION = "pan-audit: allow domain-type-shape";

const DOMAIN_PARAM_EXPECTED = new Map<string, string>([
  ["locale", "Locale"],
  ["lang", "Locale"],
  ["kind", "RoutedKind or CorpusWorkKind"],
  ["format", "DownloadFormat"],
]);

interface Candidate {
  rel: string;
  line: number;
  kind: "domain-api-primitive" | "registry-open-type";
  subject: string;
  annotation: string;
  expected: string;
  detail: string;
}

interface Baseline {
  accepted: ReadonlySet<string>;
  findings: Finding[];
}

function lineOf(sf: ts.SourceFile, node: ts.Node): number {
  return ts.getLineAndCharacterOfPosition(sf, node.getStart()).line + 1;
}

function fingerprint(candidate: Candidate): string {
  return [
    "ts",
    candidate.kind,
    candidate.rel,
    candidate.subject,
    `${candidate.annotation}->${candidate.expected}`,
  ].join(":");
}

function shouldScanTsFile(rel: string): boolean {
  if (!(rel.startsWith("src/lib/") || rel.startsWith("build/"))) return false;
  if (!rel.endsWith(".ts")) return false;
  if (rel === "build/frontmatter.ts" || rel === "build/copy-graph-payloads.ts") return false;
  if (rel === "src/lib/publication/source.ts") return false;
  if (rel.endsWith("-payload.ts")) return false;
  return true;
}

function hasExportModifier(node: ts.Node): boolean {
  if (!ts.canHaveModifiers(node)) return false;
  return (ts.getModifiers(node) ?? []).some((m) => m.kind === ts.SyntaxKind.ExportKeyword);
}

function unwrapExpression(node: ts.Expression): ts.Expression {
  let current = node;
  while (
    ts.isAsExpression(current) ||
    ts.isSatisfiesExpression(current) ||
    ts.isParenthesizedExpression(current)
  ) {
    current = current.expression;
  }
  return current;
}

function propertyNameText(name: ts.PropertyName | ts.BindingName): string | null {
  if (ts.isIdentifier(name) || ts.isStringLiteral(name) || ts.isNumericLiteral(name)) {
    return name.text;
  }
  return null;
}

function typeText(sf: ts.SourceFile, node: ts.TypeNode): string {
  return node.getText(sf).replace(/\s+/g, " ");
}

function typeContainsStringPrimitive(node: ts.TypeNode): boolean {
  if (node.kind === ts.SyntaxKind.StringKeyword) return true;
  if (ts.isUnionTypeNode(node)) {
    return node.types.some(typeContainsStringPrimitive);
  }
  if (ts.isParenthesizedTypeNode(node)) {
    return typeContainsStringPrimitive(node.type);
  }
  return false;
}

function isOpenStringRecord(node: ts.TypeNode): boolean {
  if (!ts.isTypeReferenceNode(node)) return false;
  if (node.typeName.getText() !== "Record") return false;
  const first = node.typeArguments?.[0];
  return first?.kind === ts.SyntaxKind.StringKeyword;
}

function hasSuppression(source: string, line: number): boolean {
  const lines = source.split("\n");
  const candidates = [line - 1, line - 2];
  return candidates.some((idx) => idx >= 0 && idx < lines.length && lines[idx]?.includes(SUPPRESSION));
}

function maybeAddDomainPrimitive(
  out: Candidate[],
  input: {
    rel: string;
    sf: ts.SourceFile;
    source: string;
    node: ts.Node;
    subject: string;
    name: string;
    type: ts.TypeNode | undefined;
  },
): void {
  const expected = DOMAIN_PARAM_EXPECTED.get(input.name);
  if (!expected || !input.type || !typeContainsStringPrimitive(input.type)) return;
  const line = lineOf(input.sf, input.node);
  if (hasSuppression(input.source, line)) return;
  out.push({
    rel: input.rel,
    line,
    kind: "domain-api-primitive",
    subject: input.subject,
    annotation: typeText(input.sf, input.type),
    expected,
    detail: `${input.subject} names repo domain vocabulary but uses raw string`,
  });
}

function scanFunctionLike(
  out: Candidate[],
  input: {
    rel: string;
    sf: ts.SourceFile;
    source: string;
    apiName: string;
    fn: ts.SignatureDeclarationBase;
  },
): void {
  for (const param of input.fn.parameters) {
    const name = propertyNameText(param.name);
    if (!name) continue;
    maybeAddDomainPrimitive(out, {
      rel: input.rel,
      sf: input.sf,
      source: input.source,
      node: param,
      subject: `${input.apiName}(${name})`,
      name,
      type: param.type,
    });
  }
}

function scanInterface(
  out: Candidate[],
  rel: string,
  sf: ts.SourceFile,
  source: string,
  node: ts.InterfaceDeclaration,
): void {
  for (const member of node.members) {
    if (!ts.isPropertySignature(member)) continue;
    const name = propertyNameText(member.name);
    if (!name) continue;
    maybeAddDomainPrimitive(out, {
      rel,
      sf,
      source,
      node: member,
      subject: `${node.name.text}.${name}`,
      name,
      type: member.type,
    });
  }
}

function scanTypeAlias(
  out: Candidate[],
  rel: string,
  sf: ts.SourceFile,
  source: string,
  node: ts.TypeAliasDeclaration,
): void {
  if (isOpenStringRecord(node.type)) {
    const line = lineOf(sf, node);
    if (!hasSuppression(source, line)) {
      out.push({
        rel,
        line,
        kind: "registry-open-type",
        subject: node.name.text,
        annotation: typeText(sf, node.type),
        expected: "Record<closed domain key type, ...> or a boundary-specific alias",
        detail: `${node.name.text} is exported as an open string-keyed record`,
      });
    }
  }
  if (!ts.isTypeLiteralNode(node.type)) return;
  for (const member of node.type.members) {
    if (!ts.isPropertySignature(member)) continue;
    const name = propertyNameText(member.name);
    if (!name) continue;
    maybeAddDomainPrimitive(out, {
      rel,
      sf,
      source,
      node: member,
      subject: `${node.name.text}.${name}`,
      name,
      type: member.type,
    });
  }
}

function scanVariableStatement(
  out: Candidate[],
  rel: string,
  sf: ts.SourceFile,
  source: string,
  stmt: ts.VariableStatement,
): void {
  for (const decl of stmt.declarationList.declarations) {
    const name = propertyNameText(decl.name);
    if (!name) continue;
    if (decl.type && isOpenStringRecord(decl.type)) {
      const line = lineOf(sf, decl);
      if (!hasSuppression(source, line)) {
        out.push({
          rel,
          line,
          kind: "registry-open-type",
          subject: name,
          annotation: typeText(sf, decl.type),
          expected: "Record<closed domain key type, ...>",
          detail: `${name} is exported as an open string-keyed registry`,
        });
      }
    }
    if (!decl.initializer) continue;
    const init = unwrapExpression(decl.initializer);
    if (ts.isArrowFunction(init) || ts.isFunctionExpression(init)) {
      scanFunctionLike(out, { rel, sf, source, apiName: name, fn: init });
    }
  }
}

function scanSourceFile(rel: string, source: string, sf: ts.SourceFile): Candidate[] {
  const out: Candidate[] = [];
  for (const stmt of sf.statements) {
    if (!hasExportModifier(stmt)) continue;
    if (ts.isFunctionDeclaration(stmt) && stmt.name) {
      scanFunctionLike(out, { rel, sf, source, apiName: stmt.name.text, fn: stmt });
    } else if (ts.isVariableStatement(stmt)) {
      scanVariableStatement(out, rel, sf, source, stmt);
    } else if (ts.isInterfaceDeclaration(stmt)) {
      scanInterface(out, rel, sf, source, stmt);
    } else if (ts.isTypeAliasDeclaration(stmt)) {
      scanTypeAlias(out, rel, sf, source, stmt);
    }
  }
  return out;
}

function loadBaseline(ctx: RuleContext): Baseline {
  if (!ctx.exists(BASELINE_PATH)) return { accepted: new Set(), findings: [] };
  try {
    const parsed = JSON.parse(ctx.read(BASELINE_PATH)) as { typescript?: unknown };
    const raw = parsed.typescript;
    const accepted = Array.isArray(raw)
      ? new Set(raw.filter((item): item is string => typeof item === "string"))
      : new Set<string>();
    return { accepted, findings: [] };
  } catch (err) {
    return {
      accepted: new Set(),
      findings: [
        {
          rule: TS_ID,
          severity: "warning",
          category: CATEGORY,
          file: BASELINE_PATH,
          observed: `could not parse type-domain baseline: ${String(err)}`,
          contract: "The type-domain heuristic baseline is explicit JSON data, not hidden tool state.",
          why: "If the baseline is unreadable, the heuristic cannot distinguish accepted legacy smells from new regressions.",
          repair: "Fix data/type-domain-baseline.json so it parses and contains string arrays under `typescript` and `python`.",
        },
      ],
    };
  }
}

function candidateFinding(candidate: Candidate): Finding {
  return {
    rule: TS_ID,
    severity: "warning",
    category: CATEGORY,
    file: candidate.rel,
    line: candidate.line,
    observed: `${candidate.detail}; saw \`${candidate.annotation}\`, expected ${candidate.expected}`,
    contract:
      "Inside site/library domain APIs, repo vocabulary such as locale, kind, and download format should be carried by named domain types after boundary parsing. Open string-keyed registries are acceptable only for true external maps.",
    why:
      "Raw primitives erase the ubiquitous language that agents and reviewers grep for; they also let unrelated strings pass through APIs that already have narrower domain types.",
    repair:
      "Use the existing named type, introduce a small domain alias/value type, or convert raw input at the route/adapter/parser boundary before it reaches internal APIs.",
    doNotFixBy:
      `Renaming the parameter to dodge the audit, widening the named type, or adding \`${SUPPRESSION}\` without a concrete boundary reason.`,
  };
}

function staleBaselineFinding(fingerprint: string): Finding {
  return {
    rule: TS_ID,
    severity: "info",
    category: CATEGORY,
    file: BASELINE_PATH,
    observed: `baseline fingerprint no longer appears in the TypeScript scan: ${fingerprint}`,
    contract:
      "The type-domain heuristic baseline records accepted current debt only; when code is fixed or renamed, stale fingerprints should be removed.",
    why:
      "A stale baseline turns explicit debt accounting into hidden dead data, making future audit output harder to trust.",
    repair: "Remove the stale fingerprint from data/type-domain-baseline.json.",
  };
}

function scanTypeScript(ctx: RuleContext): Candidate[] {
  return ctx.walk({ filter: shouldScanTsFile }).flatMap((rel) => {
    const source = ctx.read(rel);
    const sf = parseModule(rel, source);
    return sf ? scanSourceFile(rel, source, sf) : [];
  });
}

export const pan023TypeDomainTs: Rule = {
  id: TS_ID,
  title: "PAN023: exported TypeScript domain APIs and registries use named domain types",
  tier: "heuristic",
  run(ctx: RuleContext): Finding[] {
    const baseline = loadBaseline(ctx);
    const candidates = scanTypeScript(ctx);
    const current = new Set(candidates.map(fingerprint));
    const fresh = candidates
      .filter((candidate) => !baseline.accepted.has(fingerprint(candidate)))
      .map(candidateFinding);
    const stale = [...baseline.accepted]
      .filter((accepted) => !current.has(accepted))
      .map(staleBaselineFinding);
    return [...baseline.findings, ...fresh, ...stale];
  },
};

export const pan023TypeDomainPy: Rule = {
  id: PY_ID,
  title: "PAN023: Python domain APIs avoid new raw primitives, primitive tuple contracts, and optionality clusters",
  tier: "heuristic",
  run(ctx: RuleContext): Finding[] {
    return runPythonCheck(ctx, {
      id: PY_ID,
      category: CATEGORY,
      severity: "warning",
      script: "python/type_domain.py",
      contract:
        "Python library/audit code should keep Pancratius domain vocabulary in named types once raw external data has crossed a parser or adapter boundary. Existing debt is explicit in data/type-domain-baseline.json.",
      why:
        "Primitive tuples, open registries, and clusters of optional fields make it hard for agents and reviewers to find the domain concept and easy to pass half-validated shapes across module boundaries.",
      repair:
        "Introduce a named dataclass/NamedTuple/Literal/value object, split boundary DTOs from internal commands, or document a real parser/external-boundary exception with an inline suppression.",
      doNotFixBy:
        "Adding broad suppressions, renaming fields to dodge the vocabulary match, or replacing named domain types with wider primitives to make call sites easier.",
    });
  },
};
