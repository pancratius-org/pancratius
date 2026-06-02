# research-pure: reads src/content via fixed ir_view; writes only to scratch.
"""Build the per-line lineation task (the reconstructed-per-line CONTROL input) from the
FIXED ir_view, over the 12 calibration regions. Carries every structural role (incl. the
new `image` boundary) so models see the full skeleton, not a stripped body.

Output: data/lineation_task.json — list of {cid, book, lines:[{idx,sub,role,text,emph,wraps}]}.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ir_view as iv
import features as feat

DATA = Path(__file__).resolve().parents[1] / "data"


def main() -> int:
    cards = {c["cid"]: c for c in json.loads((DATA / "calib3_faithful.json").read_text())}
    bd = {f"{n:02d}": p for n, p in feat.book_dirs()}
    out = []
    for cid, c in cards.items():
        paras = iv.read_view(bd[c["book"]])
        lines = []
        for p in paras:
            if not (c["lo"] <= p.index <= c["hi"]):
                continue
            if p.lines:
                for li, ln in enumerate(p.lines):
                    emph = "bold" if ln.bold else ("italic" if ln.italic else "")
                    lines.append({"idx": p.index, "sub": li, "role": p.role,
                                  "text": ln.text, "emph": emph, "wraps": ln.wraps})
            else:
                lines.append({"idx": p.index, "sub": 0, "role": p.role,
                              "text": "", "emph": "", "wraps": False})
        out.append({"cid": cid, "book": c["book"], "lines": lines})
    (DATA / "lineation_task.json").write_text(json.dumps(out, ensure_ascii=False))
    from collections import Counter
    roles = Counter(ln["role"] for c in out for ln in c["lines"])
    body = sum(1 for c in out for ln in c["lines"] if ln["role"] == "body")
    print(f"rebuilt lineation_task.json: {len(out)} cards, {body} body lines")
    print("roles:", dict(roles))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
