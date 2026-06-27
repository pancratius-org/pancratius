# intent-ai

Research package for recovering **authorial intent** lost in DOCX import — so the library renders
the way the author meant, not the way the file happened to serialize.

## Why

The site builds from authored DOCX, and DOCX under-encodes structure: an author often drops a
lineated block in as many separate paragraphs instead of one paragraph with soft breaks, and the
importer can't tell from the file alone whether those paragraphs are prose, verse, a list, or
display. The product goal is **beautiful, coherent reading** — not reproducing DOCX line breaks,
not maximizing per-row label accuracy. Author DOCX choices, human labels, and model predictions are
all *evidence*; the target is rendered HTML that reads well and consistently, and errors are
position-sensitive (a wrong line inside a stanza splits one coherent unit into two broken ones).

## Two tasks

Intent inference is two orthogonal decisions over the same source:

- **lineation** (Q1, structure — *this package*): is a run flowing prose, or broken into lines?
  Per-source-line `prose | lineated`, decided at run granularity. Graduated here from the
  `lineation-core` prototype: 2,194 labels, an interpretable run-aware student, and the
  active-learning loop that produced them.
- **register** (Q2, display voice): `ordinary | verse | scripture | inset | voice`, on top of an
  already-grouped block. The production model ships at `data/models/register/` and runs behind the
  `pancratius/intent_inference` seam, which already hosts both tasks. Its research home opens here
  once register labels are acquired — until then there is no register research code to keep honest.

## How

A seeded, pool-based **active-learning loop** distills a privileged LLM **teacher panel**
(text + page-image readers) into a cheap, interpretable **student**, scored by an eval harness on
panel-independent human truth. One canonical per-line artifact (`LineRecord`), one feature producer,
hash-railed identity. The only production write is a static per-book sidecar the importer
consumes — **production never imports this package**; it consumes promoted artifacts and sidecars.

- `SPEC.md` — the locked data + algorithm contract (`LineRecord`, the truth model, the decode API).
- `ARCHITECTURE.md` — code layout (the six-stage loop) and the active-learning design.
- `experiments/` — committed lab-notebook evidence (scorecards, reports), outside the importable package.
