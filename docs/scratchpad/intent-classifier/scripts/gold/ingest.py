# research-pure: fold a human adjudication back into gold, with provenance + an audit report.
"""Consume a `responses-*.json` (human labels + per-region notes) against its `review_*.json`
sidecar and produce:
  - human_gold: the human-resolved SPLIT lines (human = authority), each tagged with provenance
    (method=blind_per_line) so prior-sensitive best-guesses are not mistaken for hard truth;
  - audit: human (blind re-label) vs the gate's accepted label, agreement + per-stratum error rate +
    the disagreements (a non-zero stratum is systematic bias → reopen);
  - reopen: regions whose audit disagreed (e.g. a shared false-accept) — sweep before trusting.

Pure: takes the parsed dicts + a stratum map; `run.py` does the file I/O.
"""
from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping


def _book(key: str) -> str:
    m = re.search(r"_b(\d+)", key)
    return m.group(1) if m else "??"


def ingest(responses: Mapping[str, dict], sidecar: Mapping[str, object],
           stratum_of: Mapping[str, str]) -> dict:
    """responses: {rid: {lines:{'idx.sub':label}, note}}. sidecar: {audit:{'rid|idx.sub':accepted},
    human:['rid|idx.sub']}. stratum_of: rid→stratum."""
    human_label = {f"{rid}|{k}": lab for rid, v in responses.items() for k, lab in v["lines"].items()}
    notes = {rid: v["note"] for rid, v in responses.items() if v.get("note")}
    audit_truth: dict[str, str] = dict(sidecar["audit"])     # type: ignore[arg-type]

    human_gold = []
    for hk in sidecar["human"]:                              # type: ignore[union-attr]
        rid, k = hk.split("|")
        idx, sub = (int(x) for x in k.split("."))
        human_gold.append({"rid": rid, "idx": idx, "sub": sub, "label": human_label.get(hk),
                           "method": "blind_per_line", "note": notes.get(rid)})

    by_stratum: dict[str, dict[str, float]] = defaultdict(lambda: {"n": 0, "wrong": 0})
    disagreements = []
    for ak, accepted in audit_truth.items():
        rid = ak.split("|")[0]
        h = human_label.get(ak)
        s = stratum_of.get(rid, "?")
        by_stratum[s]["n"] += 1
        if h is not None and h != accepted:
            by_stratum[s]["wrong"] += 1
            disagreements.append({"key": ak, "accepted": accepted, "human": h,
                                  "stratum": s, "book": _book(ak)})
    for st in by_stratum.values():
        st["error_rate"] = st["wrong"] / st["n"] if st["n"] else 0.0

    return {
        "human_gold": human_gold,
        "audit": {"n": len(audit_truth), "agree": len(audit_truth) - len(disagreements),
                  "by_stratum": dict(by_stratum), "disagreements": disagreements},
        "reopen": sorted({d["key"].split("|")[0] for d in disagreements}),
        "notes": notes,
    }
