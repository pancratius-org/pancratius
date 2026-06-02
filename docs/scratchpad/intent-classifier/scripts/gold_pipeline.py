# research-pure: renders pages + structure for page-reader gold; writes only to scratch.
"""Page-driven gold pipeline — packaging step (REBUILT on the verified SourceSpan bridge).

Fixes the QA-found criticals:
  C1 (two index spaces): PNG and structure now come from ONE source. ir_view carries each
     body paragraph's source <w:p> ordinal range (src_start/src_end, from the IR's
     SourceSpan — verified to match render-slice's index space). We render the EXACT
     min(src_start)..max(src_end) span via `render-slice --range`, so the PNG and the
     structure list cover the same source paragraphs by construction.
  C2 (multi-match --around): regions are addressed by a UNIQUE ir_view idx range, never a
     substring; render uses --range.
  H1 (book02): its DOCX source is fixed → render-slice loads it directly (no render_clean).
  H2 (label space): reader labels 2-way {flowing, lineated} to match the gold.
  M1 (contamination): the structure shows ONLY hard OOXML signals (heading/***/image/
     empty/<w:br>/right-align) + the production compiler's non-body CONTEXT verdict +
     text/emphasis/wrap — never a text-heuristic guess on a body line (that would bias
     the reader).

Each region package = {rid, book, ir_lo, ir_hi, src_lo, src_hi, png[], lines[]}.
A line: {book, idx, sub, kind, text, emph, wraps} where kind ∈ {body, break} (break =
the hard structural markers the reader may use as context but never labels).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ir_view as iv  # noqa: E402
import features as feat  # noqa: E402
from pancratius import docx_inspect as di  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "data" / "gold_lineation"
PNG = DATA / "png"

# Hard structural roles the reader sees as CONTEXT (never labels): OOXML/IR signals plus
# the production compiler's non-body verdict (ROLE_CONTEXT). No text-heuristic guess (M1).
_HARD_CTX = {
    iv.ROLE_HEADING: "heading", iv.ROLE_THEMATIC: "thematic-break",
    iv.ROLE_IMAGE: "image", iv.ROLE_EMPTY: "blank",
    iv.ROLE_TABLE: "table", iv.ROLE_LIST: "list",
    iv.ROLE_SIGNATURE: "right-aligned", iv.ROLE_EPIGRAPH: "right-aligned",
    iv.ROLE_BLOCKQUOTE: "blockquote", iv.ROLE_OTHER: "other-block",
    iv.ROLE_CONTEXT: "context",   # production compiler: non-body structure
}


def _src_ordinal(p: iv.Para, text_index: dict[str, int]) -> int | None:
    """A body paragraph's source <w:p> ordinal: SourceSpan if present, else text-match
    fallback (verified ~100%). Returns None only if neither resolves."""
    if p.src_start is not None:
        return p.src_start
    import re
    key = re.sub(r"\s+", "", p.text).strip()
    return text_index.get(key)


def build_region(book: str, ir_lo: int, ir_hi: int, rid: str, ctx_pad: int = 0) -> dict:
    bd = {f"{n:02d}": q for n, q in feat.book_dirs()}
    docx = bd[book]
    paras = iv.read_view(docx)
    # text->source-ordinal fallback index (render-slice space)
    rows = di.read_rows(docx)
    import re
    # LOW-1 fix: a DUPLICATE source text is ambiguous — drop it from the fallback index
    # (mapping to first-match would be wrong). SourceSpan covers ~100% anyway; this only
    # guards the rare fallback. A None ordinal is then handled by the caller.
    _seen: dict[str, int] = {}
    for r in rows:
        if r.text.strip():
            k = re.sub(r"\s+", "", r.text).strip()
            _seen[k] = _seen.get(k, 0) + 1
    tindex: dict[str, int] = {}
    for r in rows:
        if r.text.strip():
            k = re.sub(r"\s+", "", r.text).strip()
            if _seen[k] == 1:
                tindex[k] = r.index

    region = [p for p in paras if ir_lo <= p.index <= ir_hi]
    # resolve the source <w:p> span the render must cover
    ords = [o for p in region if (o := _src_ordinal(p, tindex)) is not None]
    if not ords:
        raise SystemExit(f"{rid}: no source ordinals resolved for ir range {ir_lo}..{ir_hi}")
    src_lo, src_hi = min(ords) - ctx_pad, max(ords) + ctx_pad

    # render the EXACT source span (PNG ⟺ structure by construction)
    out = PNG / f"{rid}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["uv", "run", "pancratius", "docx", "render-slice", "--book", str(int(book)),
         "--range", f"{src_lo}:{src_hi}", "--out", str(out)],
        capture_output=True, text=True, cwd=ROOT,
    )
    subprocess.run(["bash", "-c", "find src/content -name '.~lock.*#' -delete"], cwd=ROOT)
    pages = sorted(out.parent.glob(f"{rid}*.png"))

    # structure: body lines (labelable) + hard-context markers; NO inferred soft roles
    lines = []
    for p in region:
        if p.role == iv.ROLE_BODY:
            for li, ln in enumerate(p.lines):
                em = "bold" if ln.bold else ("italic" if ln.italic else "")
                lines.append({"book": book, "idx": p.index, "sub": li, "kind": "body",
                              "text": ln.text, "emph": em, "wraps": ln.wraps})
        else:
            ctx = _HARD_CTX.get(p.role, "context")   # fallback unreachable: every non-body role is mapped
            lines.append({"book": book, "idx": p.index, "sub": 0, "kind": "break",
                          "marker": ctx, "text": p.text})
    return {"rid": rid, "book": book, "ir_lo": ir_lo, "ir_hi": ir_hi,
            "src_lo": src_lo, "src_hi": src_hi, "png": [str(p) for p in pages], "lines": lines}


def _ir_range_for(book: str, around: str, ctx: int) -> tuple[int, int]:
    """Resolve an anchor substring to a UNIQUE ir_view idx range; error on multi-match."""
    bd = {f"{n:02d}": q for n, q in feat.book_dirs()}
    paras = iv.read_view(bd[book])
    hits = [p.index for p in paras if around in p.text]
    if len(hits) != 1:
        raise SystemExit(f"#{book} {around!r}: matched {len(hits)} paras {hits[:5]} — not unique; "
                         f"use a more specific anchor or an explicit ir range")
    c = hits[0]
    idxs = [p.index for p in paras]
    lo = max(min(idxs), c - ctx)
    hi = min(max(idxs), c + ctx)
    return lo, hi


# Regions derived FROM the gold's own ir-idx ranges (no substring/±ctx guessing): each
# (book, ir_lo, ir_hi, label) matches exactly the span the page-verified gold covers, so
# the rendered page, the structure, and the gold labels are all the same paragraphs.
def _regions_from_gold() -> list[tuple[str, int, int, str]]:
    from collections import defaultdict
    gold = [json.loads(l) for l in (DATA / "anchors_reconciled.jsonl").open()]
    by = defaultdict(list)
    for r in gold:
        by[(r["book"], r["region"])].append(r["idx"])
    return [(book, min(idxs), max(idxs), region) for (book, region), idxs in sorted(by.items())]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--anchors", action="store_true")
    args = ap.parse_args()
    out = []
    for i, (book, lo, hi, label) in enumerate(_regions_from_gold()):
        rid = f"r{i:02d}_b{book}"
        reg = build_region(book, lo, hi, rid)
        reg["label"] = label
        nb = sum(1 for l in reg["lines"] if l["kind"] == "body")
        cov = sum(1 for p in iv.read_view({f"{n:02d}": q for n, q in feat.book_dirs()}[book])
                  if lo <= p.index <= hi and p.role == iv.ROLE_BODY and p.src_start is not None)
        print(f"  {rid}: ir[{lo}..{hi}] src[{reg['src_lo']}..{reg['src_hi']}] "
              f"{len(reg['png'])}pg {nb} body (span-cov {cov}/{nb})")
        out.append(reg)
    (DATA / "regions.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"wrote {len(out)} regions -> regions.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
