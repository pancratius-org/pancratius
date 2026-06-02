# research-pure: reads src/content read-only; writes only to the scratch dir.
"""Find QA-answer regions across the corpus: a question (heading ending in '?', a
numbered '#### N. ...?' heading, or a 'Вопрос' line) followed by a short answer run.
These govern a huge share of corpus verse-blocks (24/75 books, ~2600 questions; #01
and #10 are almost entirely QA). The register decision (do QA-answers want verse?)
must be made corpus-wide, not per-book.

For each sampled region we record the paragraph indices so it can be rendered both
ways (prose vs verse) under the new prose.css and judged.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pancratius import docx_inspect as di  # noqa: E402
import features as feat  # noqa: E402

D = Path(__file__).resolve().parents[1] / "data"
_Q = re.compile(r".*[?？]\s*$")


def qa_regions_for(num: int, docx: Path, max_regions: int = 3) -> list[dict]:
    """Heading-question -> following short-answer run, from the live row signals."""
    rows = di.read_rows(docx)
    out: list[dict] = []
    n = len(rows)
    for i, r in enumerate(rows):
        if not r.heading:
            continue
        if not _Q.match(re.sub(r"\s+", " ", r.text)):
            continue
        # answer run = following non-heading paragraphs until next heading
        j = i + 1
        while j < n and not rows[j].heading:
            j += 1
        ans = [k for k in range(i + 1, j) if rows[k].text.strip()]
        if 2 <= len(ans) <= 24:  # a real short-answer run
            lo = i
            hi = min(n - 1, j - 1)
            out.append({"key": f"{num:02d}", "q_idx": i, "lo": lo, "hi": hi,
                        "n_answer": len(ans), "question": re.sub(r"\s+", " ", r.text)[:70]})
        if len(out) >= max_regions:
            break
    return out


def main() -> int:
    targets = feat.book_dirs()
    all_regions: list[dict] = []
    rng_books = [1, 10, 30, 40, 31, 35, 47, 71, 54, 28]  # QA-heavy spread
    for num, docx in targets:
        if num not in rng_books:
            continue
        regs = qa_regions_for(num, docx, max_regions=2)
        all_regions.extend(regs)
        if regs:
            print(f"  #{num:02d}: {len(regs)} QA regions (e.g. '{regs[0]['question']}')")
    (D / "qa_regions.json").write_text(json.dumps(all_regions, ensure_ascii=False, indent=1))
    print(f"wrote {len(all_regions)} QA regions across {len(rng_books)} QA-heavy books")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
