# research-pure: reads src/content via the IR; writes only to the scratch dir.
"""Faithful 3-way calibration cards, built from `ir_view` (NOT the lossy ParaRow).

Round 1 was invalidated because annotators saw flattened text — no emphasis, hard
breaks merged, headers missing. This renders each calibration region from the
IR-faithful view so the annotator (me, Светозар, a judge) sees what the author
ACTUALLY encoded:
  - every <w:br> line on its own line;
  - bold shown **bold**, italic shown *italic*;
  - the boundary skeleton labelled (HEADING / *** / pseudo-header / speaker-label /
    signature / blockquote) so a run visibly cannot cross one;
  - per-line wrap flag (W) at the real reading column.

Emits BOTH a text card (for text/LLM annotators) and an HTML render (for the eye).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ir_view as iv  # noqa: E402
import features as feat  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "data"
OUT = Path(__file__).resolve().parents[1] / "renders" / "calib_faithful"

# stratified regions (book, locate-substring, proposed 3-way, why) — same strata as
# round 1 but anchored to land on real runs (round-1 anchoring bugs fixed via ir_view).
PICKS = [
    ("13", "Олег медленно сел", "flowing", "narrative fiction: dialogue + past-tense"),
    ("16", None, "flowing", "expository prose, wraps"),
    ("25", "Что это за книга", "verse", "free verse, short lineated"),
    ("71", "Кто такой человек", "verse", "litany of parallel questions"),
    ("30", "Если Я — Един", "verse", "anaphoric invocation answer (excl. citation/markers)"),
    ("34", "Я войду в комнату", "verse", "anaphoric '— Я …' stanza"),
    ("68", "Тем, кто", "lineated-prose", "parallel list, bounded by pseudo-headers"),
    ("02", "Сергей шёл в школу", "verse", "lineated meditation"),
    ("02", "Ты странный", "flowing", "dialogue scene"),
    ("05", "В начале — Безмолвие", "lineated-prose", "numbered teaching points"),
    ("27", "Так будет и в Моём", "verse", "bold <w:br> stanza (keystone case)"),
    ("31", "Если Иисус", "lineated-prose", "QA-answer middle"),
    ("02", "Анфиса замерла", "flowing", "narrative beats split by an IMAGE boundary (image-fix case)"),
]


def find_center(paras, sub):
    if sub:
        return next((k for k, p in enumerate(paras) if sub in p.text), len(paras) // 2)
    return next((k for k, p in enumerate(paras) if p.role == iv.ROLE_BODY), 0)


# A calibration window clips at strong SECTION boundaries (heading/***/table/list/
# signature/epigraph) but SPANS image/blockquote/other — those are shown in-context as
# markers (the model must SEE the image between two beats, not have the window truncated
# at it). They still bound a RUN downstream; here they are context, not a window edge.
_CLIP_ROLES = {iv.ROLE_HEADING, iv.ROLE_THEMATIC, iv.ROLE_TABLE, iv.ROLE_LIST,
               iv.ROLE_SIGNATURE, iv.ROLE_EPIGRAPH, iv.ROLE_CONTEXT}


def region(paras, center, ctx=10):
    """Clip to the nearest strong section boundary on each side; span images/quotes."""
    lo = center
    while lo > 0 and paras[lo - 1].role not in _CLIP_ROLES and center - lo < ctx:
        lo -= 1
    hi = center
    n = len(paras)
    while hi < n - 1 and paras[hi + 1].role not in _CLIP_ROLES and hi - center < ctx:
        hi += 1
    return lo, hi


_ROLE_TAG = {
    iv.ROLE_HEADING: "═══ HEADING", iv.ROLE_THEMATIC: "═══ * * *",
    iv.ROLE_CONTEXT: "─── (compiler: context)",
    iv.ROLE_SIGNATURE: "═══ SIGNATURE", iv.ROLE_EPIGRAPH: "═══ EPIGRAPH",
    iv.ROLE_BLOCKQUOTE: "═══ QUOTE", iv.ROLE_LIST: "═══ LIST", iv.ROLE_TABLE: "═══ TABLE",
    iv.ROLE_IMAGE: "═══ [IMAGE]", iv.ROLE_OTHER: "═══ (other block)",
    iv.ROLE_EMPTY: "(blank — stanza/section break)",
}


def text_card(paras, lo, hi) -> str:
    """Annotator-facing text: emphasis shown with **/*, breaks on own lines, boundaries
    flagged. Body lines are what gets a 3-way label."""
    rows = []
    for p in paras[lo:hi + 1]:
        if p.role in _ROLE_TAG and p.role != iv.ROLE_BODY:
            tag = _ROLE_TAG[p.role]
            txt = (" " + p.text) if p.text else ""
            rows.append(f"        {tag}{txt}")
            continue
        for li, ln in enumerate(p.lines):
            t = ln.text
            if ln.bold:
                t = f"**{t}**"
            elif ln.italic:
                t = f"*{t}*"
            wrapmark = "↩W" if ln.wraps else "  "
            head = f"p{p.index:>4}" if li == 0 else "    "
            rows.append(f"  {head} {wrapmark} {t}")
    return "\n".join(rows)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    bd = {f"{n:02d}": p for n, p in feat.book_dirs()}
    cards = []
    for ci, (key, sub, proposed, why) in enumerate(PICKS):
        paras = iv.read_view(bd[key])
        center = find_center(paras, sub)
        lo, hi = region(paras, center)
        cid = f"cf{ci:02d}_book{key}"
        body = [p for p in paras[lo:hi + 1] if p.role == iv.ROLE_BODY]
        cards.append({
            "cid": cid, "book": key, "lo": paras[lo].index, "hi": paras[hi].index,
            "proposed": proposed, "why": why,
            "n_body": len(body),
            "card": text_card(paras, lo, hi),
        })
    (DATA / "calib3_faithful.json").write_text(json.dumps(cards, ensure_ascii=False, indent=1))
    # human-readable dump
    dump = []
    for c in cards:
        dump.append(f"\n{'='*72}\n{c['cid']}  book #{c['book']}  proposed={c['proposed']}  ({c['why']})\n{'='*72}\n{c['card']}")
    (OUT / "cards.txt").write_text("\n".join(dump), encoding="utf-8")
    print(f"built {len(cards)} faithful calibration cards -> data/calib3_faithful.json + renders/calib_faithful/cards.txt")
    for c in cards:
        print(f"  {c['cid']:<14} #{c['book']} proposed={c['proposed']:<14} body_lines={c['n_body']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
