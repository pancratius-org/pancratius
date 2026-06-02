# research-pure: reads scratch data; writes only to the scratch dir.
"""F4 candidate generation: for each region, render the SAME text under 4 different
grouping decisions, in the real Astro reader CSS, so a judge can compare the actual
output surface (markup + PNG).

Groupers (per paragraph -> prose | verse | struct):
  heuristic — the current `normalize.py` verdict (baseline.jsonl is_verse).
  wrap      — the structural fix: maximal runs (>=2) of short non-wrapping content
              lines (empties allowed inside as stanza breaks) become verse.
  gbt       — the 22-feature model's per-paragraph prediction.
  llm       — the wide-context LLM-segmenter consensus (judgeG_{A,B,C}), if available.

Outputs: candidates/<rid>_<method>.html (markup judging), a screenshot manifest
(candidates/manifest.json: html->png), and labelings.json (per rid/method/idx label).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier

sys.path.insert(0, str(Path(__file__).resolve().parent))
import model as M

D = Path(__file__).resolve().parents[1] / "data"
OUT = Path(__file__).resolve().parents[1] / "renders" / "candidates"

CSS = """
:root{--serif:"Source Serif 4","PT Serif",Georgia,"Times New Roman",serif;--measure:60ch;
--ink:#161a1f;--ink-soft:#424954;--accent:#a86b1f;--accent-soft:rgba(168,107,31,.55);--bg:#f4f6f9;}
*{box-sizing:border-box;}
body{background:var(--bg);margin:0;padding:2.4rem 2.6rem;font-family:var(--serif);}
.tag{font-family:ui-sans-serif,system-ui,sans-serif;font-size:.72rem;letter-spacing:.14em;
 text-transform:uppercase;color:var(--accent);font-weight:700;margin-bottom:1.3rem;}
.prose{max-width:var(--measure);margin:0;font-family:var(--serif);font-size:1.075rem;
 line-height:1.72;color:var(--ink);text-align:left;}
/* NEW prose.css (2026-05-30): book typography — first-line indent is the paragraph
   signal, vertical gap ~0; first para and first-after-break sit flush. */
.prose p{margin:0 0 .35em;text-indent:1.4em;text-wrap:pretty;}
.prose>p:first-child,.prose h3+p,.prose .verse-block+p,.prose .ornament+p{text-indent:0;}
.prose h3{font-family:var(--serif);font-weight:600;font-size:1.18rem;margin:2.1em 0 .7em;color:var(--ink);}
.prose .ornament{text-align:center;letter-spacing:.5em;color:var(--accent);margin:1.4em 0;}
.prose p.signature{text-align:right;font-style:italic;color:var(--ink-soft);margin:1.1em 0;}
.prose .verse-block{margin:1.4em 0;padding-left:1.4rem;border-left:1px solid var(--accent-soft);
 color:var(--ink-soft);font-style:italic;font-size:1.02em;line-height:1.55;white-space:pre-line;text-align:left;}
"""


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def is_struct_flag(r: dict) -> bool:
    return bool(r["empty"] or r["heading"] or r["thematic"] or r["align_right"] or r["numbered"])


def render_html(seq: list[dict], labels: dict[int, str], tag: str) -> str:
    """seq: ordered feature rows; labels: idx->{prose,verse,struct}."""
    parts: list[str] = []
    buf: list[str] = []  # verse buffer lines ("" = stanza break)

    def flush():
        nonlocal buf
        while buf and buf[-1] == "":
            buf.pop()
        while buf and buf[0] == "":
            buf.pop(0)
        if len(buf) >= 2:
            parts.append('<div class="verse-block">' + "\n".join(esc(x) for x in buf) + "</div>")
        elif len(buf) == 1:
            parts.append(f"<p>{esc(buf[0])}</p>")
        buf = []

    for r in seq:
        idx = r["idx"]
        lab = labels.get(idx, "prose")
        if r["empty"]:
            if buf:
                buf.append("")  # stanza break inside an open verse run
            continue
        if r["heading"]:
            flush(); parts.append(f"<h3>{esc(r['text'])}</h3>"); continue
        if r["thematic"]:
            flush(); parts.append('<p class="ornament">* * *</p>'); continue
        if r["align_right"]:
            flush(); parts.append(f'<p class="signature">{esc(r["text"])}</p>'); continue
        if lab == "verse" and not r["numbered"]:
            buf.append(r["text"])
        else:
            flush(); parts.append(f"<p>{esc(r['text'])}</p>")
    flush()
    return (f'<!doctype html><meta charset=utf-8><style>{CSS}</style>'
            f'<div class="tag">{esc(tag)}</div><article class="prose">{"".join(parts)}</article>')


def wrap_labels(seq: list[dict]) -> dict[int, str]:
    lab: dict[int, str] = {}
    i = 0
    n = len(seq)
    def short_content(r): return not is_struct_flag(r) and not r["wraps"]
    while i < n:
        r = seq[i]
        if is_struct_flag(r):
            lab[r["idx"]] = "struct"; i += 1; continue
        if short_content(r):
            run = []; k = i
            while k < n and (seq[k]["empty"] or short_content(seq[k])):
                if not seq[k]["empty"]:
                    run.append(k)
                k += 1
            if len(run) >= 2:
                for j in run:
                    lab[seq[j]["idx"]] = "verse"
            else:
                for j in run:
                    lab[seq[j]["idx"]] = "prose"
            i = k; continue
        lab[r["idx"]] = "prose"; i += 1
    return lab


def main() -> int:
    feats = M.load_features()
    base = {(r["key"], r["idx"]): r["is_verse"] for r in (json.loads(l) for l in (D / "baseline.jsonl").open())}
    # GBT fit on gold
    gold = [r for r in (json.loads(l) for l in (D / "gold.jsonl").open())
            if r["label"] in ("prose", "verse") and (r["key"], r["idx"]) in feats]
    X = np.array([M.featurize(feats[(r["key"], r["idx"])], False) for r in gold], float)
    y = np.array([1 if r["label"] == "verse" else 0 for r in gold])
    clf = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.08, max_iter=300,
                                         l2_regularization=1.0, min_samples_leaf=15).fit(X, y)
    # LLM consensus
    def load(f): return {(r["key"], r["idx"]): r["label"] for r in (json.loads(l) for l in (D / f).open())}
    JA, JB, JC = load("judgeG_A.jsonl"), load("judgeG_B.jsonl"), load("judgeG_C.jsonl")
    def llm_label(key, idx):
        labs = [j.get((key, idx)) for j in (JA, JB, JC)]; labs = [x for x in labs if x]
        return max(set(labs), key=labs.count) if labs else None

    regions = json.loads((D / "group_regions.json").read_text())
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = []
    labelings = {}
    for reg in regions:
        rid, key, lo, hi = reg["rid"], reg["key"], reg["lo"], reg["hi"]
        seq = [feats[(key, i)] for i in range(lo, hi + 1) if (key, i) in feats]
        methods = {}
        # heuristic
        methods["heuristic"] = {r["idx"]: ("struct" if is_struct_flag(r) else
                                           ("verse" if base.get((key, r["idx"])) else "prose")) for r in seq}
        # wrap
        methods["wrap"] = wrap_labels(seq)
        # gbt
        gbt = {}
        for r in seq:
            if is_struct_flag(r):
                gbt[r["idx"]] = "struct"
            else:
                p = clf.predict(np.array([M.featurize(r, False)], float))[0]
                gbt[r["idx"]] = "verse" if p == 1 else "prose"
        methods["gbt"] = gbt
        # llm
        methods["llm"] = {r["idx"]: (llm_label(key, r["idx"]) or methods["wrap"][r["idx"]]) for r in seq}

        labelings[str(rid)] = {m: {str(k): v for k, v in lab.items()} for m, lab in methods.items()}
        for m, lab in methods.items():
            html = render_html(seq, lab, f"#{key} · {m}")
            hp = OUT / f"r{rid:02d}_{m}.html"
            hp.write_text(html, encoding="utf-8")
            manifest.append({"html": str(hp), "png": str(OUT / f"r{rid:02d}_{m}.png"),
                             "rid": rid, "key": key, "method": m})
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    (D / "labelings.json").write_text(json.dumps(labelings, ensure_ascii=False))
    print(f"wrote {len(manifest)} candidate HTMLs over {len(regions)} regions -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
