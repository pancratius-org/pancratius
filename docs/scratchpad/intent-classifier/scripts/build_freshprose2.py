# research-only: Tier-0 batch 2 — more unseen dense/borderline prose blocks for human adjudication,
# to widen the one-block prose guardrail (per planning-agent: 3-4 distinct unseen books, ~40-60 lines).
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_adjudication as ba  # noqa: E402
from ir_view import LineKey  # noqa: E402

DATA = HERE.parent / "data" / "render_audit"
ADJ = HERE.parent / "adjudicate"
# exclude eval/anchor/reconciled + the FIRST fresh batch's books (41,24,71,30,47,01)
EXCLUDE = {"64", "69", "37", "60", "31", "28", "13", "66", "63", "67", "16", "19",
           "73", "07", "55", "17", "40", "41", "24", "71", "30", "47", "01"}


def book(rid: str) -> str:
    m = re.search(r"_b(\d+)", rid)
    return m.group(1) if m else "??"


def main() -> int:
    pkg = {e["rid"]: e for e in json.loads((DATA / "reader_pkg.json").read_text())}
    fresh = {rid: e for rid, e in pkg.items() if book(rid) not in EXCLUDE}
    wrap = [e for e in fresh.values() if e["stratum"] == "wrap_prose"]
    flat = [e for e in fresh.values() if e["stratum"] == "mid_flat"]
    chosen, seen_books, nlines = [], set(), 0

    def take(cands: list, n: int) -> None:
        nonlocal nlines
        c = 0
        for e in sorted(cands, key=lambda x: -len(x["keys"])):   # bigger dense blocks first (more prose)
            b = book(e["rid"])
            if b in seen_books or nlines >= 60 or len(e["keys"]) < 4:
                continue
            chosen.append(e)
            seen_books.add(b)
            nlines += len(e["keys"])
            c += 1
            if c >= n:
                break
    take(wrap, 4)    # dense wrapping prose — the over-lineation risk (g23/g28 type)
    take(flat, 2)    # narrative prose for breadth

    items = []
    for e in chosen:
        texts = ba._texts(e["structure"])
        items.append({
            "id": e["rid"], "mode": "per-line", "image": ba._img_data_uri(e["composite"]),
            "structure": e["structure"], "lineOptions": ba._LINEOPTS,
            "lines": [{"key": f"{k[0]}.{k[1]}", "text": texts.get(LineKey(*k), "?")} for k in e["keys"]],
            "hint": f"FRESH prose-guardrail batch 2 — book {book(e['rid'])}, stratum {e['stratum']}.",
        })
    task = {"title": "Fresh prose-guardrail batch 2 (more unseen books, dense prose)",
            "instructions": ("DOCX PAGE (left) is the authority. Label each line prose (a flowing "
                             "paragraph merely broken with Enter) or lineated (intended break). These "
                             "are dense/borderline cases from books not used to design the prompt — "
                             "exactly where over-lineation is the risk. Label what reads TRUE on the page."),
            "items": items}
    out = ADJ / "assessment_freshprose2.json"
    out.write_text(json.dumps(task, ensure_ascii=False))
    print(f"chosen ({len(items)} regions, {sum(len(i['lines']) for i in items)} lines):")
    for e in chosen:
        print(f"  {e['rid']:14} book {book(e['rid']):>2} {e['stratum']:<10} {len(e['keys'])} lines")
    print(f"wrote {out} ({out.stat().st_size/1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
