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
