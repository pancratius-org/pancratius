#!/usr/bin/env node
// Publish the conceptosphere graph payloads under public/data/.
//
// Two responsibilities, one seam:
//   1. Minify the RU-keyed source graphs (data/*.json) → public/data/*.json.
//      RU is prefix-less site-wide, so these keep their un-suffixed names.
//   2. Join each graph's topology with the authored EN overlay
//      (data/conceptosphere-i18n/en.json, { kind_prefixed_key: { label, gloss? } }
//      — `concept:<concept_id>` / `community:<key>`, so a concept id can never
//      collide with a community key) and emit
//      public/data/pancratius-{concepts,books}-graph.en.json. The EN
//      route fetches these so the client stays a one-fetch pure consumer that
//      renders labels verbatim — no runtime locale logic, no second fetch.
//
// The join is STRICT: a concept/community stable id with no EN entry throws.
// A missing translation is a build failure, never a silent RU fallback under
// an English URL (conceptosphere-bilingual-design.md §2). The PAN021 audit is
// the gate; this throw is the same contract at the publish seam so `npm run
// generate` alone never emits a half-translated payload. Book NODE titles are
// not translated here (they degrade via the "Russian original" badge, §4); but
// their COMMUNITIES and their `top_concepts[]` refs (the same concept
// vocabulary as the concepts graph) ARE joined to the EN label.

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { stderr } from "node:process";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const SRC_DIR = join(REPO_ROOT, "data");
const DST_DIR = join(REPO_ROOT, "public", "data");
const OVERLAY_FILE = join(SRC_DIR, "conceptosphere-i18n", "en.json");

interface LocaleSpec {
  /** Locale tag; drives the emitted filename suffix. */
  readonly locale: string;
  /** Absolute path to the authored overlay for this locale. */
  readonly overlayPath: string;
}

const LOCALES: readonly LocaleSpec[] = [{ locale: "en", overlayPath: OVERLAY_FILE }];

interface PayloadSpec {
  /** Source filename under data/ (and the RU public/data/ name). */
  readonly name: string;
  /**
   * Whether this graph's nodes are translatable concepts. The concepts graph's
   * nodes are concept lemmas (translated); the books graph's nodes are books
   * (untranslated — they degrade via the badge). Communities are always joined.
   */
  readonly nodesAreConcepts: boolean;
}

const PAYLOADS: readonly PayloadSpec[] = [
  { name: "pancratius-concepts-graph.json", nodesAreConcepts: true },
  { name: "pancratius-books-graph.json", nodesAreConcepts: false },
];

// ── The pure join (exported for unit tests) ─────────────────────────────

export interface OverlayEntry {
  label: string;
  gloss?: string;
}
export type Overlay = Record<string, OverlayEntry>;

interface TopConceptRef {
  concept_id?: unknown;
  label?: unknown;
  [key: string]: unknown;
}
interface GraphNode {
  id?: unknown;
  concept_id?: unknown;
  label?: unknown;
  gloss?: unknown;
  top_concepts?: unknown;
  [key: string]: unknown;
}
interface GraphCommunity {
  key?: unknown;
  label?: unknown;
  [key: string]: unknown;
}
export interface Graph {
  nodes?: GraphNode[];
  communities?: GraphCommunity[];
  [key: string]: unknown;
}

/** A graph after locale projection. Unlike the loose input `Graph` (a permissive
 *  view over raw JSON), the join always materialises `nodes` and `communities`,
 *  so the output type guarantees both rather than understating the postcondition. */
export interface LocalizedGraph extends Graph {
  nodes: GraphNode[];
  communities: GraphCommunity[];
}

function isString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

/** The stable id a concept node joins on: `concept_id`, falling back to `id`. */
function conceptStableId(node: GraphNode): string | null {
  if (isString(node.concept_id)) return node.concept_id;
  if (isString(node.id)) return node.id;
  return null;
}

/**
 * Translate a book node's `top_concepts[]` labels by stable id. A top-concept
 * whose `concept_id` has no overlay entry throws — the same strict join (and
 * the same vocabulary) as the concept nodes, so PAN021 keeps it complete.
 */
function joinBookNodeConcepts(node: GraphNode, overlay: Overlay): GraphNode {
  if (!Array.isArray(node.top_concepts)) return node;
  const top = (node.top_concepts as TopConceptRef[]).map((concept) => {
    if (!isString(concept.concept_id)) return concept;
    const entry = requireEntry(overlay, `concept:${concept.concept_id}`, "book top concept");
    return { ...concept, label: entry.label };
  });
  return { ...node, top_concepts: top };
}

function requireEntry(overlay: Overlay, stableId: string, what: string): OverlayEntry {
  const entry = overlay[stableId];
  if (!entry || !isString(entry.label)) {
    throw new Error(
      `conceptosphere EN overlay is missing a label for ${what} ${JSON.stringify(stableId)} — ` +
        "add it to data/conceptosphere-i18n/en.json (a missing translation is a build failure, not an RU fallback)",
    );
  }
  return entry;
}

/**
 * Project a RU-keyed graph onto a locale by substituting overlay labels/glosses
 * by stable id. Returns a new object; the input is not mutated. Throws on the
 * first missing translation.
 */
export function joinLocalePayload(graph: Graph, overlay: Overlay, nodesAreConcepts: boolean): LocalizedGraph {
  const nodes = (graph.nodes ?? []).map((node) => {
    if (nodesAreConcepts) {
      const conceptId = conceptStableId(node);
      if (conceptId === null) return node;
      const entry = requireEntry(overlay, `concept:${conceptId}`, "concept");
      const joined: GraphNode = { ...node, label: entry.label };
      if (entry.gloss !== undefined) joined.gloss = entry.gloss;
      return joined;
    }
    // Book nodes are not translated, but their `top_concepts[]` reference the
    // same concept vocabulary as the concepts graph; substitute the EN label by
    // `concept:<concept_id>` so the EN side panel renders English top-concepts.
    return joinBookNodeConcepts(node, overlay);
  });

  const communities = (graph.communities ?? []).map((com) => {
    // Only join communities that carry an explicit fingerprint key (the
    // generator emits it; payloads predating the regen keep their RU label and
    // are caught by the PAN021 audit once keyed).
    if (!isString(com.key)) return com;
    const entry = requireEntry(overlay, `community:${com.key}`, "community");
    return { ...com, label: entry.label };
  });

  return { ...graph, nodes, communities };
}

// ── I/O wrapper ─────────────────────────────────────────────────────────

function sameText(path: string, text: string): boolean {
  return existsSync(path) && readFileSync(path, "utf-8") === text;
}

function writeIfChanged(dst: string, text: string, label: string): void {
  if (sameText(dst, text)) return;
  writeFileSync(dst, text);
  stderr.write(`wrote ${label} -> ${relative(REPO_ROOT, DST_DIR)}/\n`);
}

function loadOverlay(path: string): Overlay {
  return JSON.parse(readFileSync(path, "utf-8")) as Overlay;
}

function localeName(name: string, locale: string): string {
  return name.replace(/\.json$/, `.${locale}.json`);
}

function readGraph(spec: PayloadSpec): Graph {
  return JSON.parse(readFileSync(join(SRC_DIR, spec.name), "utf-8")) as Graph;
}

/**
 * Write the RU payload verbatim (un-suffixed). RU is the source-of-truth and is
 * never gated by the overlay: a broken or incomplete `en.json` must not suppress
 * a RU artifact (conceptosphere-bilingual-design.md §2 — "the RU page is
 * unaffected; the RU payload never consults en.json"). So every RU payload is
 * written BEFORE any locale join runs, and a join failure can never abort it.
 */
function publishRu(graphs: ReadonlyMap<string, Graph>): void {
  for (const spec of PAYLOADS) {
    const graph = graphs.get(spec.name);
    if (graph) writeIfChanged(join(DST_DIR, spec.name), JSON.stringify(graph), spec.name);
  }
}

/**
 * Join each graph with each locale overlay and write the per-locale payload.
 * A missing overlay is skipped (that locale simply has no payload yet); a
 * present-but-incomplete/malformed overlay throws (fail loud — a missing
 * translation is a build failure, not a silent RU fallback). Returns 1 on the
 * first such failure, AFTER the RU payloads are already on disk.
 */
function publishLocales(graphs: ReadonlyMap<string, Graph>): number {
  for (const { locale, overlayPath } of LOCALES) {
    if (!existsSync(overlayPath)) continue;
    let overlay: Overlay;
    try {
      overlay = loadOverlay(overlayPath);
    } catch (err: unknown) {
      stderr.write(`invalid conceptosphere ${locale} overlay JSON in ${relative(REPO_ROOT, overlayPath)}: ${String(err)}\n`);
      return 1;
    }
    for (const spec of PAYLOADS) {
      const graph = graphs.get(spec.name);
      if (!graph) continue;
      try {
        const joined = joinLocalePayload(graph, overlay, spec.nodesAreConcepts);
        const outName = localeName(spec.name, locale);
        writeIfChanged(join(DST_DIR, outName), JSON.stringify(joined), outName);
      } catch (err: unknown) {
        stderr.write(`${err instanceof Error ? err.message : String(err)}\n`);
        return 1;
      }
    }
  }
  return 0;
}

function main(): number {
  mkdirSync(DST_DIR, { recursive: true });
  const missing = PAYLOADS.filter((spec) => !existsSync(join(SRC_DIR, spec.name)));
  if (missing.length) {
    stderr.write(
      `conceptosphere payloads missing in data/: ${missing.map((s) => s.name).join(", ")} ` +
        "- run `uv run pancratius conceptosphere graph generate` to regenerate\n",
    );
    return 1;
  }

  const graphs = new Map<string, Graph>();
  for (const spec of PAYLOADS) {
    try {
      graphs.set(spec.name, readGraph(spec));
    } catch (err: unknown) {
      stderr.write(`invalid graph payload JSON in data/${spec.name}: ${String(err)}\n`);
      return 1;
    }
  }

  // RU first and unconditionally; the overlay join can only fail AFTER RU is safe.
  publishRu(graphs);
  return publishLocales(graphs);
}

// Only run the I/O wrapper when invoked as a script, not when imported by tests.
if (import.meta.url === `file://${process.argv[1]}`) {
  process.exit(main());
}
