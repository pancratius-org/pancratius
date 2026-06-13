// PAN021 — conceptosphere EN translation completeness (docs/audit-harness.md).
//
// The bilingual conceptosphere serves `/en/conceptosphere` in English by
// joining the RU-keyed graph topology with an authored overlay
// `data/conceptosphere-i18n/en.json` ({ stable_id: { label, gloss? } }) at
// build time. The contract (conceptosphere-bilingual-design.md §2): a missing
// EN translation is a BUILD FAILURE, never a silent RU fallback under an
// English URL.
//
// This rule IS the drift detector. It enumerates every stable id present in the
// committed graphs — each concept's `concept_id` (the lemma; falls back to the
// node `id`, which equals the lemma) and each community's content-fingerprint
// `key` — and fires fatal for any that lacks an entry in en.json. Overlay keys
// encode the KIND so a concept_id can never collide with a community key:
// `concept:<concept_id>` and `community:<key>`. A drifted
// community gets a new `key`, has no entry, and fires here; there is no separate
// detector. This mirrors the tag-localization audit's INTENT and fail-semantics
// (glossary-as-source-of-truth, untranslated key = hard failure) but is its own
// `core`-tier rule against its own data source — the folded tag rule is
// heuristic/warning and by contract can never gate CI.
//
// Communities are checked only when they carry an explicit `key` (the generator
// emits it; payloads predating the regen do not). Concepts are always checked.

import type { Rule, RuleContext } from "../lib/rule.ts";
import type { Finding } from "../lib/finding.ts";

const ID = "PAN021-conceptosphere-i18n";
const CATEGORY = "conceptosphere-i18n";
const I18N_FILE = "data/conceptosphere-i18n/en.json";

interface GraphSource {
  /** Repo-relative path to the committed graph payload. */
  readonly file: string;
  /**
   * Whether this graph's NODES are translatable concepts. The concepts graph's
   * nodes are concept lemmas (translated); the books graph's nodes are BOOKS
   * (they degrade via the "Russian original" badge, §4 — never translated here).
   * Both graphs' COMMUNITIES are always checked.
   */
  readonly nodesAreConcepts: boolean;
}

const GRAPH_SOURCES: readonly GraphSource[] = [
  { file: "data/pancratius-concepts-graph.json", nodesAreConcepts: true },
  { file: "data/pancratius-books-graph.json", nodesAreConcepts: false },
];

interface ConceptNode {
  id?: unknown;
  concept_id?: unknown;
  label?: unknown;
}

interface Community {
  key?: unknown;
  label?: unknown;
}

interface Graph {
  nodes?: unknown;
  communities?: unknown;
}

interface RequiredId {
  /** The kind-prefixed overlay key (`concept:<id>` / `community:<key>`) that must have an EN entry. */
  readonly stableId: string;
  /** The entity kind (for the finding text). */
  readonly entity: string;
  /** A human label (RU) so the finding names what is untranslated. */
  readonly ruLabel: string;
  /** Which graph file it came from. */
  readonly file: string;
}

function isString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

/** Every concept `concept_id` (fallback `id`) in a graph payload. */
function conceptIds(graph: Graph, file: string): RequiredId[] {
  if (!Array.isArray(graph.nodes)) return [];
  const out: RequiredId[] = [];
  for (const raw of graph.nodes) {
    const node = raw as ConceptNode;
    const conceptId = isString(node.concept_id) ? node.concept_id : isString(node.id) ? node.id : null;
    if (conceptId === null) continue;
    out.push({
      stableId: `concept:${conceptId}`,
      entity: "concept",
      ruLabel: isString(node.label) ? node.label : conceptId,
      file,
    });
  }
  return out;
}

/** Every community `key` in a graph payload (skips payloads without keys). */
function communityKeys(graph: Graph, file: string): RequiredId[] {
  if (!Array.isArray(graph.communities)) return [];
  const out: RequiredId[] = [];
  for (const raw of graph.communities) {
    const com = raw as Community;
    if (!isString(com.key)) continue;
    out.push({
      stableId: `community:${com.key}`,
      entity: "community",
      ruLabel: isString(com.label) ? com.label : com.key,
      file,
    });
  }
  return out;
}

function requiredIds(ctx: RuleContext): RequiredId[] | Finding {
  const ids: RequiredId[] = [];
  for (const source of GRAPH_SOURCES) {
    if (!ctx.exists(source.file)) continue;
    let graph: Graph;
    try {
      graph = JSON.parse(ctx.read(source.file)) as Graph;
    } catch (err: unknown) {
      return parseFinding(source.file, `graph payload is not valid JSON: ${String(err)}`);
    }
    if (source.nodesAreConcepts) ids.push(...conceptIds(graph, source.file));
    ids.push(...communityKeys(graph, source.file));
  }
  return ids;
}

/** A key counts as translated only when its value is { label: non-empty string }.
 *  A malformed entry (bare string, empty/absent label) is NOT a translation: the
 *  build-time join throws on exactly that shape (`requireEntry`), so the audit
 *  must agree or it would pass a build it cannot complete. Such keys are excluded
 *  here and fire the missing-translation finding below. */
function isTranslatedEntry(value: unknown): boolean {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const label = (value as { label?: unknown }).label;
  return typeof label === "string" && label.length > 0;
}

function loadOverlayKeys(ctx: RuleContext): Set<string> | Finding {
  if (!ctx.exists(I18N_FILE)) {
    return new Set<string>(); // absent overlay → every id is missing (fires below)
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(ctx.read(I18N_FILE));
  } catch (err: unknown) {
    return parseFinding(I18N_FILE, `EN overlay is not valid JSON: ${String(err)}`);
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    return parseFinding(I18N_FILE, "EN overlay must be a flat object { stable_id: { label, gloss? } }");
  }
  const translated = new Set<string>();
  for (const [key, value] of Object.entries(parsed)) {
    if (isTranslatedEntry(value)) translated.add(key);
  }
  return translated;
}

export const pan021ConceptosphereI18n: Rule = {
  id: ID,
  title:
    "PAN021: every conceptosphere stable id (concept_id, community key) must have an EN translation in data/conceptosphere-i18n/en.json",
  tier: "core",
  run(ctx: RuleContext): Finding[] {
    const ids = requiredIds(ctx);
    if (!Array.isArray(ids)) return [ids];
    const overlay = loadOverlayKeys(ctx);
    if (!(overlay instanceof Set)) return [overlay];

    return ids
      .filter((req) => !overlay.has(req.stableId))
      .map((req) => missingFinding(req));
  },
};

function missingFinding(req: RequiredId): Finding {
  return {
    rule: ID,
    severity: "fatal",
    category: CATEGORY,
    file: I18N_FILE,
    observed: `${req.entity} stable id ${JSON.stringify(req.stableId)} ("${req.ruLabel}") is present in ${req.file} but has no entry in ${I18N_FILE}`,
    contract: `The EN conceptosphere payload is the RU graph topology joined with ${I18N_FILE} ({ stable_id: { label, gloss? } }) at build time; every concept_id and community key in the committed graph MUST have an EN entry. A missing translation is a build failure, not a silent RU fallback.`,
    why: `Without an EN entry the /en/conceptosphere graph would render this ${req.entity}'s Russian label as its primary content under an English URL — the "one URL = one resource" lie the i18n contract forbids. The audit gates the build so the EN page is never a Russian resource behind English chrome.`,
    repair: `Add ${JSON.stringify(req.stableId)} to ${I18N_FILE} with an English { "label": "…" } (and an optional "gloss"). The translation campaign is additive — re-running it translates only the missing ids.`,
    doNotFixBy: `Reintroducing a runtime RU fallback (\`i18n[id]?.label ?? ru_label\`) or downgrading this rule below fatal — that is the exact silent-Russian-leak the design rejected.`,
  };
}

function parseFinding(file: string, observed: string): Finding {
  return {
    rule: ID,
    severity: "fatal",
    category: CATEGORY,
    file,
    observed,
    contract: `PAN021 reads the committed graph payloads and ${I18N_FILE} to verify EN translation completeness; both must be parseable JSON of the expected shape.`,
    why: `An unparseable graph or overlay means the build-time join cannot run and the completeness check is blind — a stale premise is worse than a loud failure.`,
    repair: `Fix the JSON at the named file so it parses (graph: { nodes, communities }; overlay: flat { stable_id: { label, gloss? } }).`,
    doNotFixBy: `Skipping the file or loosening the parse to silence the error instead of repairing the data.`,
  };
}
