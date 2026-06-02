# research-only: turn the contested panel lines into an assessment_task.json for the UI.
"""Bridge the gold pipeline -> the adjudication app (adjudicate/adjudicate.html). Emits a
per-line task with two kinds of item:
  - REAL SPLITS: a body line voted by >= `agree` readers with no `agree`-strong majority
    (genuine disagreement). A line voted by FEWER than `agree` readers is a coverage gap,
    not disagreement, and is left out (it belongs in a thin-vote repair bucket).
  - AUDIT: every line of the fully-gold audit regions, for a blind spot-check that consensus
    isn't shared bias.
The human (PNG = authority) decides; the result is ground-truth for those lines AND the basis
for ranking each reader by closeness. Each item carries the composite image (data-URI) + the
full per-line structure; the panel-vote tally rides along as a hint the app hides by default.

Run: uv run --with pillow python build_adjudication.py --set gold_block2 --agree 6 \
         --tags careful rhythm grok gemini owl deepseek mimo minimax
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ir_view import LineKey

DATA_ROOT = Path(__file__).resolve().parents[1] / "data"
ADJ = Path(__file__).resolve().parents[1] / "adjudicate"
_LINEOPTS = [{"value": "prose", "label": "Prose"}, {"value": "lineated", "label": "Lineated"}]
# The reader listing emits one body line as "  <idx>.<sub>  <wrap> | <text>" (see
# gold_build._structure); this recovers (LineKey -> text) from that display form.
_LINE = re.compile(r"^\s*(\d+)\.(\d+)\s+\S+\s*\|\s*(.*)$")


def _img_data_uri(path: str, max_w: int = 2200) -> str:
    from PIL import Image
    im = Image.open(path).convert("RGB")
    if im.width > max_w:
        im = im.resize((max_w, round(im.height * max_w / im.width)))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=82)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _texts(structure: str) -> dict[LineKey, str]:
    out: dict[LineKey, str] = {}
    for ln in structure.splitlines():
        if m := _LINE.match(ln):
            out[LineKey(int(m.group(1)), int(m.group(2)))] = m.group(3).strip()
    return out


def main(dataset: str, tags: list[str], agree: int) -> int:
    data = DATA_ROOT / dataset
    pkg = {r["rid"]: r for r in json.loads((data / "reader_pkg.json").read_text())}
    present = [t for t in tags if (data / f"reader_{t}.jsonl").exists()]
    if missing := [t for t in tags if t not in present]:
        print(f"  ! no reader file for {missing} — using {len(present)}-reader panel")
    readers = {t: {(x["rid"], x["idx"], x["sub"]): x["label"]
                   for x in map(json.loads, (data / f"reader_{t}.jsonl").read_text().splitlines())}
               for t in present}
    n_panel = len(present)
    items = []
    for rid, info in pkg.items():
        texts = _texts(info["structure"])
        rows = []
        for k in info["keys"]:
            line = LineKey(*k)
            key = (rid, line.idx, line.sub)
            cnt = Counter(readers[t][key] for t in present if key in readers[t])
            top = cnt.most_common(1)[0][1] if cnt else 0
            # a REAL split needs enough coverage (>= agree votes) AND no supermajority. A
            # line voted by fewer than `agree` readers is a coverage gap (missing data), not
            # disagreement — it belongs in the thin-vote repair bucket, not the human queue.
            if sum(cnt.values()) >= agree and top < agree:
                nl, npr = cnt.get("lineated", 0), cnt.get("prose", 0)
                rows.append({"key": f"{line.idx}.{line.sub}", "text": texts.get(line, "?"),
                             "hint": f"panel: {nl} lineated / {npr} prose"})
        if not rows:
            continue
        items.append({
            "id": rid, "mode": "per-line", "image": _img_data_uri(info["composite"]),
            "structure": info["structure"], "lineOptions": _LINEOPTS, "lines": rows,
            "hint": f"{len(rows)} lines the {n_panel}-reader panel split on.",
        })

    # Audit: spot-check fully-gold regions (consensus can share bias). Present ALL their body
    # lines for a BLIND re-label (no per-line hint); compared to consensus afterward.
    for ar in json.loads((data / "audit_queue.json").read_text()):
        info = pkg[ar["rid"]]
        texts = _texts(info["structure"])
        items.append({
            "id": f"audit_{ar['rid']}", "mode": "per-line", "image": _img_data_uri(info["composite"]),
            "structure": info["structure"], "lineOptions": _LINEOPTS,
            "lines": [{"key": f"{k[0]}.{k[1]}", "text": texts.get(LineKey(*k), "?")} for k in info["keys"]],
            "hint": f"AUDIT — blind spot-check of consensus-gold region {ar['rid']}.",
        })
    out = ADJ / f"assessment_{dataset}.json"
    task = {
        "title": f"Lineation adjudication — {dataset} contested lines",
        "instructions": ("The DOCX PAGE (left column of each image) is the authority. For each "
                         "line decide what reads TRUE: prose (a flowing paragraph the author "
                         "merely broke with Enter) or lineated (the break is intended — verse / "
                         "litany / prayer / vow / enumerated sequence; joining it would damage "
                         "the reading). Hints (panel votes) are hidden — decide from the page."),
        "items": items,
    }
    out.write_text(json.dumps(task, ensure_ascii=False))
    nlines = sum(len(i["lines"]) for i in items)
    print(f"wrote {out} — {len(items)} regions, {nlines} contested lines, {out.stat().st_size/1e6:.1f} MB")
    return 0


_PILOT_TAGS = ["careful", "rhythm", "opus", "grok", "gemini", "owl", "deepseek", "mimo", "minimax"]

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", dest="dataset", required=True, help="data subdir (e.g. gold_block2)")
    ap.add_argument("--tags", nargs="+", default=_PILOT_TAGS, help="panel reader tags")
    ap.add_argument("--agree", type=int, default=7, help="votes-agreeing threshold below which a line is contested")
    args = ap.parse_args()
    raise SystemExit(main(args.dataset, args.tags, args.agree))
