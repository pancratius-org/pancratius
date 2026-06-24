import type { Rule, RuleContext } from "../lib/rule.ts";
import type { Finding } from "../lib/finding.ts";
import { runPythonCheck } from "../lib/python.ts";

export const pan010DocxIntegrity: Rule = {
  id: "PAN010-docx-integrity",
  title: "PAN010: source DOCX packages are valid and not duplicated",
  tier: "core",
  run(ctx: RuleContext): Finding[] {
    return runPythonCheck(ctx, {
      id: "PAN010-docx-integrity",
      category: "conversion-fidelity",
      severity: "fatal",
      script: "docx_integrity.py",
      contract:
        "Committed source DOCX files are valid OOXML packages and do not contain exact duplicated body text or duplicate embedded media payloads.",
      why: "DOCX is the source of truth. A broken or accidentally duplicated source silently corrupts every derived Markdown/PDF/EPUB artifact.",
      repair:
        "Restore the affected DOCX from the author/source archive or regenerate it through the first-class DOCX tooling, then re-import and rebuild derived artifacts.",
      doNotFixBy:
        "Treating Pandoc readability as sufficient; source DOCX integrity must be checked before trusting derived content.",
    });
  },
};

export const pan025TranslatedDocxTransfer: Rule = {
  id: "PAN025-translated-docx-transfer",
  title:
    "PAN025: translated DOCX footnotes are valid and drawing metadata has no Cyrillic",
  tier: "core",
  run(ctx: RuleContext): Finding[] {
    return runPythonCheck(ctx, {
      id: "PAN025-translated-docx-transfer",
      category: "translated-docx-transfer",
      severity: "fatal",
      script: "python/translated_docx_transfer.py",
      contract:
        "Committed translated work DOCX files are source after bootstrap. Body footnote references must be positive integer IDs with matching positive definitions in the same package. Drawing names, descriptions, and titles must not contain Cyrillic donor-language text.",
      why: "A broken footnote table or donor-language drawing label can ship from the DOCX even when imported Markdown looks correct.",
      repair:
        "Regenerate or repair the affected translated DOCX through the DOCX transfer tooling, then re-import/check the sibling Markdown. If the DOCX was manually edited, fix the footnote references, definitions, or drawing metadata in the document itself.",
      doNotFixBy:
        "Suppressing the audit, deleting footnote definitions, or editing only Markdown while leaving the committed translated DOCX package inconsistent.",
    });
  },
};
