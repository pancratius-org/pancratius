# research-pure: reads src/content read-only; writes only to the scratch dir.
"""Build the 3-way calibration set: ~14 stratified RUNS, each rendered both as
lineated-prose and as verse under the new prose.css, with its DOCX signals, a
proposed 3-way label, and a one-sentence justification.

A RUN here = a maximal span of content paragraphs bounded by headings / *** /
table; empties kept inside (candidate stanza breaks). We pick runs that exemplify
each stratum so the {flowing, lineated-prose, verse} rubric is tested on real pages.
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
import wrap as wrapmod  # noqa: E402
import features as feat  # noqa: E402
import gen_candidates as G  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "renders" / "calib"
DATA = Path(__file__).resolve().parents[1] / "data"

# Hand-picked exemplar regions per stratum: (book, anchor_text_substring, proposed, why)
# anchor lets us locate a stable region; we expand to the bounded run around it.
PICKS = [
    ("13", "Олег медленно сел", "flowing", "narrative fiction: dialogue + past-tense action; wraps; breaks are incidental"),
    ("16", None, "flowing", "expository prose (prose-heavy book); paragraphs wrap"),
    ("25", "Что это за книга", "verse", "free verse: short lineated lines, removing breaks destroys the form"),
    ("71", "Кто такой человек", "verse", "litany of parallel questions; the parallelism IS the structure"),
    ("30", "Если Я — Един", "verse", "anaphoric invocation answer; rhetorical-question parallelism"),
    ("34", "Я войду в комнату", "verse", "anaphoric '— Я …' stanza; constitutive parallel structure"),
    ("68", "Тем, кто", "lineated-prose", "parallel 'Тем, кто…' list; lineated but a list, not a poem"),
    ("02", "Сергей шёл в школу", "verse", "the '02 verse-block region (lineated meditation)"),
    ("02", "Ты странный", "flowing", "dialogue scene: em-dash turns, speech; flowing narrative"),
    ("05", "В начале — Безмолвие", "lineated-prose", "numbered teaching points; lineated enumeration, not verse"),
    ("10", "Вопрос: Что такое", "verse", "QA-answer book; sample a hard-break answer run"),
    ("27", "Я есмь", "verse", "heavy <w:br> book; authored lineation is ground truth"),
    ("54", "развод в Моих глазах", "lineated-prose", "QA-answer that reads as teaching prose broken to lines"),
    ("31", "Если Иисус", "lineated-prose", "QA-answer; ambiguous middle exemplar"),
]

# Strata that must NOT appear (data-quality guards on the picked region).
_MIN_CONTENT = 4
_TOC_RE = re.compile(r"\s\d{2,4}\s*$")  # "...Глава 12. ... 375" trailing page number


def bounded_run(rows, geom, anchor, max_span=22):
    """Find the bounded run (between heading/*** ) containing/near anchor."""
    idxs = [r.index for r in rows]
    if anchor:
        hits = [r.index for r in rows if anchor in r.text]
        center = hits[0] if hits else len(rows) // 2
    else:
        # first sizable content run
        center = next((r.index for r in rows if not r.empty and not r.heading), 0)
        # advance to a run of >=4 short content lines
        for r in rows:
            if r.empty or r.heading or r.thematic:
                continue
            ws = wrapmod.wrap_stat(r.text, geom)
            if not ws.wraps:
                center = r.index
                break
    # expand to nearest bounding heading/*** on both sides
    def is_bound(r):
        return r.heading or r.thematic
    lo = center
    while lo > 0 and not is_bound(rows[lo - 1]) and center - lo < max_span:
        lo -= 1
    hi = center
    n = len(rows)
    while hi < n - 1 and not is_bound(rows[hi + 1]) and hi - center < max_span:
        hi += 1
    return lo, hi


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    bd = {f"{n:02d}": p for n, p in feat.book_dirs()}
    cards = []
    manifest = []
    for ci, (key, anchor, proposed, why) in enumerate(PICKS):
        docx = bd.get(key)
        if not docx:
            continue
        rows = di.read_rows(docx)
        geom = wrapmod.page_geom(docx)
        lo, hi = bounded_run(rows, geom, anchor)
        seq = []
        for r in rows:
            if lo <= r.index <= hi:
                ws = wrapmod.wrap_stat(r.text, geom)
                seq.append({"idx": r.index, "text": r.text, "empty": r.empty,
                            "heading": r.heading, "thematic": r.thematic,
                            "align_right": r.align in ("right", "end"), "numbered": r.numbered,
                            "wraps": ws.wraps, "fill": ws.fill, "br_count": r.br_count})
        # render both ways: lineated-prose (breaks, no wrapper) vs verse (wrapper)
        def labels(mode):
            out = {}
            for r in seq:
                if r["empty"] or r["heading"] or r["thematic"] or r["align_right"] or r["numbered"]:
                    out[r["idx"]] = "struct"
                elif r["wraps"]:
                    out[r["idx"]] = "prose"      # flowing always
                else:
                    out[r["idx"]] = "verse" if mode == "verse" else "prose"
            return out
        cid = f"calib{ci:02d}_book{key}"
        for mode in ("lineatedprose", "verse"):
            # lineated-prose mode: render short non-wrap as <p> too (new CSS shows stacked
            # short paragraphs); verse mode wraps them. (flowing always <p>.)
            html = G.render_html(seq, labels("prose" if mode == "lineatedprose" else "verse"),
                                 f"#{key} · {mode} · proposed={proposed}")
            hp = OUT / f"{cid}_{mode}.html"
            hp.write_text(html, encoding="utf-8")
            manifest.append({"html": str(hp), "png": str(OUT / f"{cid}_{mode}.png")})
        # signal summary
        content = [r for r in seq if not (r["empty"] or r["heading"] or r["thematic"])]
        # data-quality guards: skip degenerate or TOC-like regions
        toc_like = sum(1 for r in content if _TOC_RE.search(re.sub(r"\s+", " ", r["text"]))) >= max(3, len(content) // 2)
        if len(content) < _MIN_CONTENT or toc_like:
            print(f"  SKIP {cid}: content={len(content)} toc_like={toc_like} (degenerate region)")
            # remove the just-written renders from manifest
            manifest = [m for m in manifest if cid not in m["html"]]
            continue
        sig = {
            "n_content": len(content),
            "n_wrap": sum(r["wraps"] for r in content),
            "n_br": sum(1 for r in content if r["br_count"]),
            "n_empty": sum(1 for r in seq if r["empty"]),
            "mean_fill": round(sum(r["fill"] for r in content) / max(1, len(content)), 2),
        }
        cards.append({"cid": cid, "book": key, "lo": lo, "hi": hi, "stratum_proposed": proposed,
                      "why": why, "signals": sig,
                      "text_preview": [re.sub(r"\s+", " ", r["text"])[:70] for r in content[:8]]})
    (DATA / "calib3_cards.json").write_text(json.dumps(cards, ensure_ascii=False, indent=1))
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    print(f"built {len(cards)} calibration cards, {len(manifest)} renders")
    for c in cards:
        print(f"  {c['cid']:<18} proposed={c['stratum_proposed']:<14} "
              f"content={c['signals']['n_content']} wrap={c['signals']['n_wrap']} "
              f"br={c['signals']['n_br']} fill={c['signals']['mean_fill']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
