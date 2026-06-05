// Non-blocking content-quality + conversion-fidelity audits, folded from the
// existing Python checks under audit/ (docs/audit-harness.md →
// "Relationship To Existing Audits"). These are `heuristic` tier: they run ONLY
// in `npm run audit:agent` (agent guidance), NEVER gate `npm run audit:repo`
// core) or a deploy. So they are classified — given a stable id, severity, and
// category — without being promoted to fatal: the doc keeps style/quality/
// fidelity smells non-blocking until one proves a hard contract and earns a
// fixture (poetry-stanza fidelity is promoted separately in poetry.ts).
//
// They are folded read-only and referenced IN PLACE (audit/<name>.py),
// not adapted into audit/python/: each is an unmodified working audit
// that computes its own repo root, so the normalizer just runs it and turns its
// PASS/FAIL into a finding of the classified severity. Adapting one (root
// override + fixture) is the step that accompanies any future promotion to fatal.
//
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
    id: "PAN006C-tag-localization",
    script: "tag_consistency.py",
    severity: "warning",
    category: "content-i18n",
    contract: "Tags are per-entry and language-bound: a Russian entry carries the normalized canonical tag key, its English translation carries the English label (audit/data/tag-glossary.json). Video playlist titles used as tags follow the same rule.",
    why: "An unglossaried or wrongly-cased tag leaks Russian onto an English page and splinters the per-locale filter into duplicate chips for one concept.",
    repair: "Add the canonical RU key + EN label to audit/data/tag-glossary.json, then normalize the entry's tags/playlist titles to match.",
  },
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
    id: "PAN006B-lineated-wrappers",
    script: "verse_blocks.py",
    severity: "info",
    category: "conversion-fidelity",
    contract: "Converter-owned lineated wrappers contain natural source lines and blank stanza lines, not hand-authored <p>/<br> markup (content-model.md).",
    why: "Malformed lineated wrappers render wrong and teach the wrong source shape.",
    repair: "Regenerate the lineated block from the DOCX AST through the converter.",
  },
  {
    id: "PAN006B-book-verse",
    script: "book_verse.py",
    severity: "warning",
    category: "conversion-fidelity",
    contract: "Legacy diagnostic for book verse-register wrappers under the old conservative source-run rule. It is not the Q1 lineation oracle and not the split IR spec; every reported mismatch must be classified as Q1 lineation loss, Q2 register disagreement, or stale legacy-rule overreach before action.",
    why: "Over-wrapped prose ships in the wrong voice; missed register may flatten a litany. The audit also keeps watching the signature/epigraph right-alignment drift class, but it cannot decide the new flowing/lineated-prose/verse ontology by itself.",
    repair: "Inspect the DOCX source and rendered surface, classify the delta, then fix the owning layer: Q1 lineation, Q2 register promotion, or the legacy audit/golden expectation. Do not update committed Markdown solely to satisfy this diagnostic.",
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
