// Work/project boundary (docs/audit-harness.md → "PAN004: Work/Project
// Boundary"). Books and poems are corpus works; projects are themed sections.
// The type system + zod schemas already enforce most of PAN004 at `astro check`
// time — `CorpusWorkKind = "book" | "poem"` types the download routes, the
// per-collection `kind: z.literal(...)` rejects a project in books/poetry, and
// `KIND_DIRS: Record<"book" | "poem">` types the bulk archive. These two rules
// add ONLY the leaks the type system can't see: string literals and type
// widening that tsc accepts.
//
// Rule A — `getCollection("X")` takes a string literal, so adding
// `getCollection("projects")` to the work-pair corpus builder in works.ts
// COMPILES while silently pulling projects into the corpus that feeds downloads,
// feed, search, and bulk.
// Rule B — `KIND_DIRS` is a `Record` whose key type can be widened; adding a
// `project` key (after widening the Record type to satisfy tsc) COMPILES while
// putting projects into `all-md.zip`.
//
// Both derive their premise from the source of truth (collections export +
// COLLECTION_OF for A; CORPUS_WORK_KINDS + KIND_DIRS for B). If an anchor is missing
// the premise is stale: emit ONE fatal "update PAN004" finding and return,
// rather than scan with a wrong assumption (the PAN002 stale-premise pattern).

import ts from "typescript";

import type { Rule, RuleContext } from "../lib/rule.ts";
import type { Finding } from "../lib/finding.ts";
import { runPythonCheck } from "../lib/python.ts";
import {
  parseModule,
  findCallStringArgs,
  findLocalNamesForImport,
  objectLiteralKeysOf,
  objectLiteralStringValuesOf,
  arrayLiteralStringValuesOf,
} from "../lib/ast.ts";

const CATEGORY = "work-project-boundary";

const CONTENT_CONFIG = "src/content.config.ts";
const KINDS = "src/lib/kinds.ts";
const WORKS = "src/lib/works.ts";
const BULK_ARCHIVES = "build/bulk-archives.ts";

const COLLECTIONS_CONST = "collections";
const COLLECTION_OF_CONST = "COLLECTION_OF";
const CORPUS_WORK_KINDS_CONST = "CORPUS_WORK_KINDS";
const KIND_DIRS_CONST = "KIND_DIRS";
const WORK_PAIR_KIND_TYPE = "WorkPairKind";

const WHY =
  "projects are themed sections, not corpus works; pulling them into the corpus/bulk gives them phantom download/feed/search/archive presence they must not have.";

/** 1-based line of a node within its source file. */
function lineOf(sf: ts.SourceFile, node: ts.Node): number {
  return ts.getLineAndCharacterOfPosition(sf, node.getStart()).line + 1;
}

/** Parse a repo-relative module through the context, or null if it's absent. */
function parse(ctx: RuleContext, rel: string): ts.SourceFile | null {
  return ctx.exists(rel) ? parseModule(rel, ctx.read(rel)) : null;
}

/** The single "premise stale — update PAN004" finding shared by both rules. */
function stalePremise(id: string, file: string, observed: string): Finding {
  return {
    rule: id,
    severity: "fatal",
    category: CATEGORY,
    file,
    observed,
    contract: `PAN004 derives its work/project boundary from the source of truth (the \`${COLLECTIONS_CONST}\` export in ${CONTENT_CONFIG} and \`${COLLECTION_OF_CONST}\` in ${WORKS} for the corpus collections; \`${CORPUS_WORK_KINDS_CONST}\` in ${KINDS} and \`${KIND_DIRS_CONST}\` in ${BULK_ARCHIVES} for the bulk archive). The anchor it reads is missing or no longer has the expected shape.`,
    why: `The rule can no longer derive which collections/kinds are works, so it would silently stop catching projects leaking into the corpus or bulk archive — a stale premise is worse than a loud failure.`,
    repair: `Update PAN004 in audit/rules/projects.ts (and docs/audit-harness.md PAN004) to the anchor's new shape, then re-confirm it reads the work/project boundary from the source of truth.`,
    doNotFixBy: `Deleting this guard — it exists precisely so the rule can't go silently stale when the SoT moves.`,
  };
}

// Rule A — PAN004-corpus-collections ----------------------------------------

const RULE_A_ID = "PAN004-corpus-collections";

/**
 * The work-pair corpus builder (src/lib/works.ts) must read ONLY the work
 * content collections. A `getCollection("projects")` (or any non-work
 * collection) is a string literal that compiles, silently pulling projects into
 * the corpus that feeds downloads, feed, search, and bulk.
 */
export const pan004CorpusCollections: Rule = {
  id: RULE_A_ID,
  title:
    "PAN004: the work-pair corpus builder (works.ts) must read only work content collections, never projects",
  tier: "core",
  run(ctx: RuleContext): Finding[] {
    const findings: Finding[] = [];

    const configSf = parse(ctx, CONTENT_CONFIG);
    const worksSf = parse(ctx, WORKS);

    // ALL collections = the property-name keys of the `collections` export.
    const allCollections = configSf ? objectLiteralKeysOf(configSf, COLLECTIONS_CONST) : [];
    // WORK collections = the string-literal values of `COLLECTION_OF`.
    const workCollections = worksSf
      ? objectLiteralStringValuesOf(worksSf, COLLECTION_OF_CONST)
      : [];

    // Stale-premise guard: if either anchor is missing, the boundary can't be
    // derived — fail loud with one finding rather than scan on a wrong premise.
    if (allCollections.length === 0) {
      findings.push(
        stalePremise(
          RULE_A_ID,
          CONTENT_CONFIG,
          `${CONTENT_CONFIG} has no \`export const ${COLLECTIONS_CONST} = { … }\` object literal to derive the full collection set from`,
        ),
      );
      return findings;
    }
    if (workCollections.length === 0) {
      findings.push(
        stalePremise(
          RULE_A_ID,
          WORKS,
          `${WORKS} has no \`${COLLECTION_OF_CONST}\` object literal with string-literal values to derive the work collections from`,
        ),
      );
      return findings;
    }

    const work = new Set(workCollections);
    const nonWork = new Set(allCollections.filter((c) => !work.has(c)));

    // worksSf is non-null here (workCollections came from it).
    const sf = worksSf!;
    // Recognize getCollection under any local alias bound by a named import
    // (`import { getCollection as gc }` → gc), closing the aliased-import evasion;
    // the literal name is always included for the un-aliased case.
    const getCollectionNames = findLocalNamesForImport(sf, "getCollection");
    getCollectionNames.add("getCollection");
    const calls = [...getCollectionNames].flatMap((name) => findCallStringArgs(sf, name));
    for (const call of calls) {
      if (!nonWork.has(call.value)) continue;
      findings.push({
        rule: RULE_A_ID,
        severity: "fatal",
        category: CATEGORY,
        file: WORKS,
        line: lineOf(sf, call.node),
        observed: `${WORKS} calls getCollection("${call.value}") — "${call.value}" is a non-work content collection (work collections are ${[...work].map((c) => `"${c}"`).join(", ")}), so it enters the work-pair corpus`,
        contract: `The work-pair corpus builder in ${WORKS} must read ONLY the work content collections — the string-literal values of \`${COLLECTION_OF_CONST}\` (${[...work].map((c) => `"${c}"`).join(", ")}). \`getCollection("X")\` takes a string literal, so a non-work collection compiles silently; the work/project boundary is derived from the \`${COLLECTIONS_CONST}\` export and \`${COLLECTION_OF_CONST}\`, not type-checked here.`,
        why: WHY,
        repair: `Read projects via src/lib/projects.ts (their own section model) instead of through the work-pair corpus; the corpus builder must only \`getCollection\` the work collections.`,
        doNotFixBy: `Widening the work-pair collection set (or \`${WORK_PAIR_KIND_TYPE}\`) to make projects "fit" the work machinery — projects must stay a section, not become corpus works.`,
      });
    }

    return findings;
  },
};

// Rule B — PAN004-bulk-archive-kinds ----------------------------------------

const RULE_B_ID = "PAN004-bulk-archive-kinds";

/**
 * The bulk corpus archive (all-md.zip) ships WORKS ONLY. `KIND_DIRS` in
 * build/bulk-archives.ts maps the archived kinds; widening its `Record`
 * type to add a `project` key compiles and would put projects into all-md.zip.
 * Its keys must be a subset of the work kinds (`CORPUS_WORK_KINDS`).
 */
export const pan004BulkArchiveKinds: Rule = {
  id: RULE_B_ID,
  title:
    "PAN004: the bulk archive's KIND_DIRS keys must be a subset of CORPUS_WORK_KINDS — no projects in all-md.zip",
  tier: "core",
  run(ctx: RuleContext): Finding[] {
    const findings: Finding[] = [];

    const kindsSf = parse(ctx, KINDS);
    const bulkSf = parse(ctx, BULK_ARCHIVES);

    // WORK KINDS = the members of the routed-kind module's corpus-work tuple.
    const workKinds = kindsSf ? arrayLiteralStringValuesOf(kindsSf, CORPUS_WORK_KINDS_CONST) : [];
    // ARCHIVE KINDS = the keys of the `KIND_DIRS` object literal.
    const archiveKeys = bulkSf ? objectLiteralKeysOf(bulkSf, KIND_DIRS_CONST) : [];

    // Stale-premise guard: CORPUS_WORK_KINDS missing/not a string-literal tuple, or
    // KIND_DIRS not found — the boundary can't be derived.
    if (workKinds.length === 0) {
      findings.push(
        stalePremise(
          RULE_B_ID,
          KINDS,
          `${KINDS} has no \`export const ${CORPUS_WORK_KINDS_CONST} = ["…", …] as const\` string-literal tuple to derive the work kinds from`,
        ),
      );
      return findings;
    }
    if (archiveKeys.length === 0) {
      findings.push(
        stalePremise(
          RULE_B_ID,
          BULK_ARCHIVES,
          `${BULK_ARCHIVES} has no \`${KIND_DIRS_CONST}\` object literal to derive the archived kinds from`,
        ),
      );
      return findings;
    }

    const work = new Set(workKinds);
    for (const key of archiveKeys) {
      if (work.has(key)) continue;
      findings.push({
        rule: RULE_B_ID,
        severity: "fatal",
        category: CATEGORY,
        file: BULK_ARCHIVES,
        observed: `${BULK_ARCHIVES} \`${KIND_DIRS_CONST}\` has a "${key}" key — "${key}" is not a work kind (${CORPUS_WORK_KINDS_CONST} is ${workKinds.map((k) => `"${k}"`).join(" | ")}), so projects would ship in all-md.zip`,
        contract: `The bulk corpus archive (all-md.zip) ships WORKS ONLY, so \`${KIND_DIRS_CONST}\` keys in ${BULK_ARCHIVES} must be a subset of the work kinds — the members of \`${CORPUS_WORK_KINDS_CONST}\` in ${KINDS} (${workKinds.map((k) => `"${k}"`).join(", ")}). Widening that \`Record\` type to add a key compiles, so the boundary is derived here, not type-checked.`,
        why: WHY,
        repair: `Keep \`${KIND_DIRS_CONST}\` to the work kinds and reference projects only through the projects section surfaces (src/lib/projects.ts) — never the bulk corpus archive.`,
        doNotFixBy: `Widening \`${CORPUS_WORK_KINDS_CONST}\` (or the \`${KIND_DIRS_CONST}\` Record type) to make projects "fit" the work machinery instead of keeping them a section.`,
      });
    }

    return findings;
  },
};

// Rule C — PAN004-duplicate-identity ----------------------------------------

/**
 * Corpus identity uniqueness: no two content files may claim the same
 * (kind, number, lang). `src/lib/works.ts` keys per-language entries as
 * `bucket[lang] = entry`, so a second file with the same identity silently
 * OVERWRITES the first — a work vanishing from the corpus with no error. The zod
 * schema validates each file alone and can't see the collision, so it's
 * type-uncaught. Wraps the Python frontmatter scan.
 */
export const pan004DuplicateIdentity: Rule = {
  id: "PAN004-duplicate-identity",
  title: "PAN004: no two content files may claim the same (kind, number, lang)",
  tier: "core",
  run(ctx: RuleContext): Finding[] {
    return runPythonCheck(ctx, {
      id: "PAN004-duplicate-identity",
      category: CATEGORY,
      severity: "fatal",
      script: "python/work_identity.py",
      contract:
        "A work is paired across languages by (kind, number), keyed per language by lang. Each (kind, number, lang) must belong to exactly one file — works.ts buckets entries as `bucket[lang] = entry`, so a collision silently overwrites.",
      why: "A duplicate (kind, number, lang) makes one of the colliding works silently disappear from the corpus — and therefore from downloads, feed, search, and the bulk archive — with no build error.",
      repair:
        "Give each work a unique number within its kind (renumber the duplicate), or merge the two files if they are the same work. The check prints every colliding identity and the files that claim it.",
      doNotFixBy:
        "Leaving two files with the same (kind, number, lang) and relying on whichever happens to win the bucket overwrite.",
    });
  },
};
