# research-only: fresh prose-guardrail adjudication batch from books NOT used in eval/anchors.
"""Pulls prose-leaning + prose-with-.sub regions from non-reconciled books, renders them as a
per-line adjudication task (same format as build_adjudication) for the human to label. The result
becomes an UNSEEN prose guardrail to test whether v5 over-lineates fresh prose."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_adjudication as ba  # noqa: E402  (_img_data_uri, _texts, _LINEOPTS)
from ir_view import LineKey  # noqa: E402

DATA = HERE.parent / "data" / "render_audit"
ADJ = HERE.parent / "adjudicate"
# books used in eval / anchors / reconciliation — exclude so the batch is genuinely unseen
EXCLUDE = {"64", "69", "37", "60", "31", "28", "13", "66", "63", "67", "16", "19",
           "73", "07", "55", "17", "40"}


def book(rid: str) -> str:
    m = re.search(r"_b(\d+)", rid)
    return m.group(1) if m else "??"


def has_sub(e: dict) -> bool:
    return any(k[1] > 0 for k in e["keys"])


def main() -> int:
    pkg = {e["rid"]: e for e in json.loads((DATA / "reader_pkg.json").read_text())}
    fresh = {rid: e for rid, e in pkg.items() if book(rid) not in EXCLUDE}
    # prefer prose-leaning strata; ensure some prose-with-.sub regions are included
    prose_strata = {"wrap_prose", "mid_flat"}
    wrap = [e for e in fresh.values() if e["stratum"] == "wrap_prose"]
    flat = [e for e in fresh.values() if e["stratum"] == "mid_flat"]
    withsub = [e for e in fresh.values() if has_sub(e) and e["stratum"] in prose_strata]
    # pick a spread: 2 dense wrap_prose + 2 narrative mid_flat + up to 2 prose-with-.sub, ~30 lines
    chosen, seen, seen_books, nlines = [], set(), set(), 0
    def take(cands: list, n: int) -> None:
        nonlocal nlines
        c = 0
        for e in sorted(cands, key=lambda x: len(x["keys"])):   # smaller first → more books, less load
            b = book(e["rid"])
            if e["rid"] in seen or b in seen_books or nlines >= 55 or len(e["keys"]) < 4:
                continue
            chosen.append(e)
            seen.add(e["rid"])
            seen_books.add(b)
            nlines += len(e["keys"])
            c += 1
            if c >= n:
                break
    take(withsub, 1)   # prose-with-.sub if any exist in fresh books (likely none)
    take(wrap, 3)      # dense wrapping prose (the main over-lineation risk), distinct books
    take(flat, 3)      # narrative prose, distinct books

    items = []
    for e in chosen:
        texts = ba._texts(e["structure"])
        items.append({
            "id": e["rid"], "mode": "per-line", "image": ba._img_data_uri(e["composite"]),
            "structure": e["structure"], "lineOptions": ba._LINEOPTS,
            "lines": [{"key": f"{k[0]}.{k[1]}", "text": texts.get(LineKey(*k), "?")} for k in e["keys"]],
            "hint": f"FRESH prose-guardrail batch — book {book(e['rid'])}, stratum {e['stratum']}.",
        })
    task = {
        "title": "Fresh prose-guardrail batch (unseen books)",
        "instructions": ("The DOCX PAGE (left of each image) is the authority. For each line decide "
                         "prose (a flowing paragraph the author merely broke with Enter) or lineated "
                         "(the break is intended — joining would damage the reading). These are from "
                         "books NOT used to design the prompt; label what reads TRUE on the page."),
        "items": items,
    }
    out = ADJ / "assessment_freshprose.json"
    out.write_text(json.dumps(task, ensure_ascii=False))
    print(f"chosen regions ({len(items)}, {sum(len(i['lines']) for i in items)} lines):")
    for e in chosen:
        print(f"  {e['rid']:14} book {book(e['rid']):>2} {e['stratum']:<10} "
              f"{len(e['keys'])} lines  sub={'Y' if has_sub(e) else 'n'}")
    print(f"wrote {out} ({out.stat().st_size/1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
