// Non-blocking content-quality + conversion-fidelity audits, folded from the
// existing Python checks under scripts/audit/ (docs/audit-harness.md →
// "Relationship To Existing Audits"). These are `heuristic` tier: they run ONLY
// in `npm run audit:agent` (agent guidance), NEVER gate `npm run audit` (the PR
// core) or a deploy. So they are classified — given a stable id, severity, and
// category — without being promoted to fatal: the doc keeps style/quality/
// fidelity smells non-blocking until one proves a hard contract and earns a
// fixture (e.g. content-model.md says the poetry-stanza audit SHOULD become a
// fatal data-loss gate — a documented promotion candidate, not yet fatal here).
//
// They are folded read-only and referenced IN PLACE (scripts/audit/<name>.py),
// not adapted into scripts/audit/python/: each is an unmodified working audit
// that computes its own repo root, so the normalizer just runs it and turns its
// PASS/FAIL into a finding of the classified severity. Adapting one (root
// override + fixture) is the step that accompanies any future promotion to fatal.
//
// `source_coverage.py` is deliberately NOT here: it is a legacy-dependent local
// library audit (it fails on retired `legacy/` source), so it must never gate
// CI — it stays a local/manual diagnostic (see docs/audit-harness.md).

import type { Rule, Tier } from "../lib/rule.ts";
import type { Severity, Finding } from "../lib/finding.ts";
import type { RuleContext } from "../lib/rule.ts";
import { runPythonCheck } from "../lib/python.ts";

interface FoldedAudit {
  id: string;
  script: string;
  severity: Severity;
  category: string;
  contract: string;
  why: string;
  repair: string;
}

const FOLDED: readonly FoldedAudit[] = [
  {
    id: "PAN006B-formatting-artifacts",
    script: "formatting_artifacts.py",
    severity: "warning",
    category: "content-formatting",
    contract: "Authored Markdown carries no conversion formatting artifacts (stray escapes, doubled punctuation, leftover wrappers).",
    why: "Artifacts read as noise to humans and pollute the public text export and search index.",
    repair: "Clean the flagged spans in the source Markdown (or fix the converter pass that produced them).",
  },
  {
    id: "PAN008-toc-leaks",
    script: "toc_leaks.py",
    severity: "warning",
    category: "content-leak",
    contract: "A work body does not embed its own table of contents — navigation is the site's job, not the canonical text's.",
    why: "A leaked TOC ships into the public Markdown/TXT export and reads as duplicated chrome.",
    repair: "Remove the TOC block from the source Markdown body.",
  },
  {
    id: "PAN008-bibliography-leaks",
    script: "bibliography_leaks.py",
    severity: "warning",
    category: "content-leak",
    contract: "Long catalog/bibliography tables live in bibliography.yaml, not the Markdown body (content-model.md).",
    why: "A bibliography catalog in the body ships as reader-facing text and as 'read next' noise instead of structured provenance.",
    repair: "Lift the catalog into the work's bibliography.yaml sidecar and drop it from the body.",
  },
  {
    id: "PAN010-rights-boilerplate",
    script: "rights_boilerplate.py",
    severity: "info",
    category: "content-quality",
    contract: "Per-work bodies don't repeat global rights/license boilerplate — the license lives once on /license/ (architecture.md).",
    why: "Repeated boilerplate is duplicated copy that drifts and bloats every export.",
    repair: "Remove the boilerplate from the work body; rely on the shared license surface.",
  },
  {
    id: "PAN006B-title-language",
    script: "title_language.py",
    severity: "info",
    category: "i18n-content",
    contract: "EN entries carry no stale title-fallback schema fields; an RU-fallback EN title is the honest translation.source signal, not a flag (i18n-routing.md).",
    why: "A stale title-fallback field is dead metadata that misleads future i18n work.",
    repair: "Remove the legacy field; let translation.source carry the machine-translation signal.",
  },
  {
    id: "PAN010-dialogue-counts",
    script: "dialogue_counts.py",
    severity: "info",
    category: "content-quality",
    contract: "Dialogue-heavy works keep their expected turn structure after conversion.",
    why: "A large turn-count drop hints the converter flattened dialogue structure.",
    repair: "Inspect the flagged work's conversion against its source.",
  },
  {
    id: "PAN010-docx-semantics",
    script: "docx_semantics.py",
    severity: "info",
    category: "conversion-fidelity",
    contract: "Converted Markdown preserves the semantic structure the DOCX expressed (headings, emphasis, signatures).",
    why: "Lost DOCX semantics degrade the reading and the export silently.",
    repair: "Review the flagged work's converter output against the source DOCX.",
  },
  {
    id: "PAN007-size-budget",
    script: "size_budget.py",
    severity: "warning",
    category: "size-budget",
    contract: "Source content (and the built site) stay within the size budget the static host can serve.",
    why: "Oversized assets blow the host's ceiling and slow the reader; large source images may exceed the asset policy.",
    repair: "Cap/optimize the flagged assets per the import asset policy.",
  },
  {
    id: "PAN006B-poetry-stanzas",
    script: "poetry_stanzas.py",
    severity: "warning",
    category: "conversion-fidelity",
    contract: "Converted poetry Markdown matches the DOCX stanza structure — empty paragraphs are stanza breaks (content-model.md). Promotion candidate: content-model.md says this SHOULD be a fatal data-loss gate once adapted with a fixture.",
    why: "A stanza-boundary mismatch is verse data loss — the poem's lineation is content, not formatting.",
    repair: "Re-convert reading the DOCX AST stanza signal; do not blanket-collapse blank lines.",
  },
  {
    id: "PAN006B-verse-blocks",
    script: "verse_blocks.py",
    severity: "info",
    category: "conversion-fidelity",
    contract: "Converter-owned verse-block wrappers contain natural source lines and blank stanza lines, not hand-authored <p>/<br> markup (content-model.md).",
    why: "Malformed verse blocks render wrong and teach the wrong source shape.",
    repair: "Regenerate the verse block from the DOCX AST through the converter.",
  },
  {
    id: "PAN006B-book-verse",
    script: "book_verse.py",
    severity: "warning",
    category: "conversion-fidelity",
    contract: "Book verse-block decisions are faithful to the DOCX source: the converter wraps a verse-block only for a confident source verse run (>=2 short lineated lines whose lineation comes from a hard <w:br/> or a stanza-break-separated run of short standalone paragraphs), never an isolated short line, a Speaker:/Speaker (qual): label, a prose-length line, or one prose sentence after a label; and it does not leave a confident source run as prose. The DOCX-source oracle for BOOK verse (poems have poetry_stanzas.py) and the executable spec for ir_normalize's verse detection.",
    why: "An over-wrapped label/prose line ships as mis-rendered verse; a missed litany run loses authored lineation. A signature/epigraph that no longer matches the right-aligned source is the C1/I2 regression class this also guards.",
    repair: "Re-run reading the DOCX AST stanza/lineation signal (the rule in scripts/audit/book_verse.py); fix the ir_normalize verse-detection pass, not the committed Markdown. A genuine borderline run boundary is an editorial call for the lead, not a tool change.",
  },
  {
    id: "PAN006B-source-text-fidelity",
    script: "source_text_fidelity.py",
    severity: "warning",
    category: "conversion-fidelity",
    contract: "Converted Markdown preserves the source DOCX text (no dropped or duplicated passages).",
    why: "A fidelity gap means the published work silently differs from the author's source.",
    repair: "Compare the flagged work's Markdown to its DOCX and re-convert the affected span.",
  },
];

const DO_NOT_FIX_BY =
  "Silencing the audit or weakening it; these are non-blocking guidance — fix the content or, if a check proves a hard contract, adapt it (root override + fixture) and promote it to fatal.";

function fold(audit: FoldedAudit): Rule {
  const tier: Tier = "heuristic";
  return {
    id: audit.id,
    title: `${audit.id}: ${audit.category} (folded ${audit.script}, non-blocking)`,
    tier,
    run(ctx: RuleContext): Finding[] {
      return runPythonCheck(ctx, {
        id: audit.id,
        category: audit.category,
        severity: audit.severity,
        script: audit.script,
        contract: audit.contract,
        why: audit.why,
        repair: audit.repair,
        doNotFixBy: DO_NOT_FIX_BY,
      });
    },
  };
}

export const contentQualityRules: readonly Rule[] = FOLDED.map(fold);
