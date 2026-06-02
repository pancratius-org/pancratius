# research-pure: reads src/content read-only; writes only to the scratch dir.
"""Heuristic baseline — the current `normalize.py` verse detector's per-paragraph
verdict, recorded so it can be scored against gold on identical paragraphs.

For each book it runs the LIVE importer pipeline (`adapt` -> `normalize`) via
`docx_inspect.annotate`, which assigns each source paragraph the IR block kind its
reading text landed in. We record `is_verse = (block_kind == "VerseBlock")`. This is
the thing the new classifier must beat.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pancratius import docx_inspect as di  # noqa: E402
import features as feat  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "data"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    outp = OUT / "baseline.jsonl"
    total = 0
    with outp.open("w", encoding="utf-8") as f:
        for num, docx in feat.book_dirs():
            key = f"{num:02d}"
            try:
                rows = di.read_rows(docx)
                di.annotate(rows, docx)
            except Exception as e:  # noqa: BLE001
                print(f"  #{key}: FAILED {type(e).__name__}: {e}")
                continue
            for r in rows:
                f.write(json.dumps({
                    "key": key, "idx": r.index,
                    "block_kind": r.block_kind,
                    "is_verse": r.block_kind == "VerseBlock",
                }, ensure_ascii=False) + "\n")
            total += len(rows)
            print(f"  #{key}: {len(rows)} rows", flush=True)
    print(f"wrote {total} baseline verdicts -> {outp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
