# research-only: page-reader gold pilot. Reads PNG + structure, labels 2-way lineation.
"""Page-reader validation pilot. A reader (Sonnet) sees, per region: the rendered PNG
(the page — authority for how it READS) and the faithful structure (per-line text +
emphasis + wrap + hard boundary markers). It labels each body line flowing/lineated.
We measure Cohen's kappa vs the page-verified human gold — the GATE before scaling.

This script only PACKAGES the per-reader prompt + collects/merges results that reader
agents write. It does not call an API itself (the readers are spawned as agents that can
view the PNG via the Read tool). Use `package` to emit reader inputs; `score` to compare
collected reader labels (data/gold_lineation/reader_<tag>.jsonl) to the gold.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data" / "gold_lineation"

READER_BRIEF = """You are adjudicating LINEATION for a spiritual book whose author pressed
Enter at the end of every line — so the raw line breaks do NOT reliably show his intent.
For each BODY line decide how it was meant to read:
  - "flowing": part of a flowing prose paragraph (sentences run on and wrap; the break is
    just the author's Enter habit). Narrative and dialogue are flowing even in short lines.
  - "lineated": the break is INTENDED — verse, litany, invocation, a list, a deliberately
    broken sequence — where removing the break would damage the structure.

EVIDENCE: you are given (1) the rendered PAGE image(s) — the authority for how the text
actually reads (wrapping, stanza gaps, layout); and (2) the per-line STRUCTURE (text,
emphasis, whether the line wraps, and hard structural markers: heading / thematic-break /
image / blank / right-aligned / blockquote). NOTE: an image renders on the page as a
blank GAP — trust the STRUCTURE's "image"/"blank"/"heading" markers as the boundary
authority where the page is ambiguous. Use the page for reading-feel, the structure for
exact boundaries. Label ONLY body lines. Judge what reads true; do not over-lineate prose."""


def package() -> int:
    regions = json.loads((DATA / "regions.json").read_text())
    out = []
    for reg in regions:
        body = [l for l in reg["lines"] if l["kind"] == "body"]
        ctx_lines = []
        for l in reg["lines"]:
            if l["kind"] == "body":
                w = "WRAPS" if l["wraps"] else "nowrap"
                em = f" {l['emph']}" if l["emph"] else ""
                ctx_lines.append(f"  BODY (idx={l['idx']},sub={l['sub']}) {w}{em} | {l['text']}")
            else:
                ctx_lines.append(f"  --- [{l['marker']}] {l.get('text','')[:50]}")
        out.append({"rid": reg["rid"], "book": reg["book"], "png": reg["png"],
                    "keys": [(l["idx"], l["sub"]) for l in body],
                    "context": "\n".join(ctx_lines)})
    (DATA / "reader_inputs.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"packaged {len(out)} regions for the reader -> reader_inputs.json")
    print(f"body lines total: {sum(len(r['keys']) for r in out)}")
    return 0


def score(tags: list[str]) -> int:
    import importlib
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from sklearn.metrics import cohen_kappa_score, confusion_matrix  # noqa
    gold = {(g["book"], g["idx"]): g["lineation"]
            for g in (json.loads(l) for l in (DATA / "anchors_reconciled.jsonl").open())}
    # collapse reader (idx,sub) -> paragraph: lineated if ANY sub lineated (a <w:br> para
    # is lineated as a unit) — matches the gold's paragraph grain.
    from collections import defaultdict, Counter
    for tag in tags:
        fp = DATA / f"reader_{tag}.jsonl"
        if not fp.exists():
            print(f"{tag}: (no file {fp.name})"); continue
        by = defaultdict(list)
        for r in (json.loads(l) for l in fp.open()):
            by[(r["book"], r["idx"])].append(r["lineation"])
        pred = {k: ("lineated" if "lineated" in v else "flowing") for k, v in by.items()}
        keys = [k for k in gold if k in pred]
        if not keys:
            print(f"{tag}: 0 overlap with gold"); continue
        y = [gold[k] for k in keys]; p = [pred[k] for k in keys]
        agree = sum(a == b for a, b in zip(y, p)) / len(keys)
        k = cohen_kappa_score(y, p)
        print(f"{tag}: n={len(keys)} agree={agree:.3f} κ={k:.3f}  "
              f"confusion[gold flow/lin]={confusion_matrix(y,p,labels=['flowing','lineated']).tolist()}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("package")
    s = sub.add_parser("score"); s.add_argument("tags", nargs="+")
    args = ap.parse_args()
    if args.cmd == "package":
        return package()
    return score(args.tags)


if __name__ == "__main__":
    raise SystemExit(main())
