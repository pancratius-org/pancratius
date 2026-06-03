# research-only: combined WIDE prose-guardrail adjudication batch (8 unseen books) — closes the
# QA "one-block prose" gap. 5 freshly-rendered wrap_prose (dense prose) + 3 rendered mid_flat.
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_adjudication as ba  # noqa: E402
from ir_view import LineKey  # noqa: E402

DATA = HERE.parent / "data"
ADJ = HERE.parent / "adjudicate"
MIDFLAT = {"g01_b57", "g16_b38", "g17_b14"}   # already-rendered fresh narrative (render_audit)


def book(rid: str) -> str:
    m = re.search(r"_b(\d+)", rid)
    return m.group(1) if m else "??"


def item(e: dict, note: str) -> dict:
    texts = ba._texts(e["structure"])
    return {"id": e["rid"], "mode": "per-line", "image": ba._img_data_uri(e["composite"]),
            "structure": e["structure"], "lineOptions": ba._LINEOPTS,
            "lines": [{"key": f"{k[0]}.{k[1]}", "text": texts.get(LineKey(*k), "?")} for k in e["keys"]],
            "hint": f"{note} — book {book(e['rid'])}, stratum {e['stratum']}."}


def main() -> int:
    pw = {e["rid"]: e for e in json.loads((DATA / "prosewide/reader_pkg.json").read_text())}
    ra = {e["rid"]: e for e in json.loads((DATA / "render_audit/reader_pkg.json").read_text())}
    items = [item(e, "dense wrapping prose (over-lineation risk)") for e in pw.values()]
    items += [item(ra[r], "fresh narrative") for r in MIDFLAT if r in ra]
    task = {"title": "Wide prose-guardrail batch (8 unseen books)",
            "instructions": ("DOCX PAGE (left) is the authority. Label each line prose (a flowing "
                             "paragraph merely broken with Enter) or lineated (intended break). These "
                             "are from books NOT used to design the prompt; the dense wrapping ones are "
                             "exactly where over-lineation is the risk. Label what reads TRUE on the page."),
            "items": items}
    out = ADJ / "assessment_prosewide.json"
    out.write_text(json.dumps(task, ensure_ascii=False))
    nbooks = len({book(i["id"]) for i in items})
    print(f"wrote {out}: {len(items)} regions / {nbooks} books / "
          f"{sum(len(i['lines']) for i in items)} lines ({out.stat().st_size/1e6:.1f} MB)")
    for i in items:
        print(f"  {i['id']:10} book {book(i['id']):>2} {len(i['lines'])} lines")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
