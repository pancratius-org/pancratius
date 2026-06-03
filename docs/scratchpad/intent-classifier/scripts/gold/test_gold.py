# research-pure: unit tests for the gold core. Stdlib only — `python3 -m gold.test_gold`.
"""Locks the gate, block, and audit behavior the gold rebuild depends on, including the boundary
cases where a false-accept would hide. No network, no substrate."""
from __future__ import annotations

from . import aggregate as agg
from . import audit, blocks
from .types import AuditLine, Gates, LineKey, Reason, Status, normalize_label

G = Gates()
K = LineKey


def _votes(*labs: str, conf: float | None = 0.9) -> list[tuple[str, float | None]]:
    return [(lab, conf) for lab in labs]


# ---- reader_verdict (gate-strict over reps) --------------------------------------------------

def test_reader_verdict() -> None:
    assert agg.reader_verdict([]) is None
    assert agg.reader_verdict(["prose"]) == "prose"            # lone first rep stands
    assert agg.reader_verdict(["prose", "lineated"]) is None    # 2-rep tie abstains
    assert agg.reader_verdict(["prose", "prose"]) == "prose"
    assert agg.reader_verdict(["prose", "prose", "lineated"]) == "prose"   # 3-rep strict majority
    assert agg.reader_verdict(["prose", "lineated", "lineated", "prose"]) is None  # 2-2 tie
    assert agg.reader_verdict(["prose"] * 3 + ["lineated"] * 2) == "prose"  # 5-rep 3-2 resolves
    assert agg.reader_verdict(["prose"] * 2 + ["lineated"] * 2) is None     # 4-rep 2-2 abstains


def test_lead_confidence() -> None:
    assert agg.lead_confidence([("prose", 0.8), ("prose", 0.6)], "prose") == 0.7
    assert agg.lead_confidence([("prose", None)], "prose") is None     # unrecorded ≠ confident
    assert agg.lead_confidence([("prose", 0.9)], None) is None
    assert agg.lead_confidence([("prose", 0.9), ("lineated", 0.1)], "prose") == 0.9  # verdict reps only


def test_panel_majority() -> None:
    assert agg.panel_majority({"grok": "prose", "gemini-pro": "prose", "ds-flash-text": "lineated"}, G) == "prose"
    assert agg.panel_majority({"grok": "prose", "gemini-pro": "lineated"}, G) is None  # 1-1 tie
    assert agg.panel_majority({"grok": None, "gemini-pro": None, "ds-flash-text": None}, G) is None


# ---- decide_line: acceptance + the boundaries where false-accept hides ------------------------

def test_accept_unanimous() -> None:
    d = agg.decide_line(K(1, 0), {r: _votes("lineated") for r in G.core}, gates=G)
    assert d.status is Status.ACCEPT and d.label == "lineated" and d.reasons == ()


def test_accept_exactly_two_thirds_including_lead() -> None:
    # min_core_agree boundary: lead + one agree, third dissents → still ACCEPT (2/3, lead in majority)
    reps = {"grok": _votes("prose"), "gemini-pro": _votes("prose"), "ds-flash-text": _votes("lineated")}
    d = agg.decide_line(K(1, 0), reps, gates=G)
    assert d.status is Status.ACCEPT and d.label == "prose" and d.reasons == ()


def test_two_thirds_agree_but_lead_dissents_escalates() -> None:
    # 2/3 agree on lineated but the LEAD says prose → spec forbids accept (lead must be in majority)
    reps = {"grok": _votes("prose"), "gemini-pro": _votes("lineated"), "ds-flash-text": _votes("lineated")}
    d = agg.decide_line(K(1, 0), reps, gates=G)
    assert d.status is Status.ESCALATE and Reason.GROK_PANEL_SPLIT in d.reasons
    assert d.panel_majority == "lineated" and d.lead_label == "prose"


def test_low_conf_escalates_then_routes() -> None:
    d1 = agg.decide_line(K(1, 0), {r: _votes("prose", conf=0.5) for r in G.core}, gates=G)
    assert d1.status is Status.ESCALATE and Reason.LOW_CONF in d1.reasons
    d3 = agg.decide_line(K(1, 0), {r: _votes("prose", "prose", "prose", conf=0.5) for r in G.core}, gates=G)
    assert d3.status is Status.ROUTE_HUMAN   # 3 reps reached, still low-conf


def test_core_abstain_is_genuine_not_missing() -> None:
    # a reader VOTED (2 reps) but they disagree → genuine abstain, not an operational gap
    reps = {"grok": _votes("prose"), "gemini-pro": _votes("prose"),
            "ds-flash-text": _votes("prose", "lineated")}
    d = agg.decide_line(K(1, 0), reps, gates=G)
    assert Reason.CORE_ABSTAIN in d.reasons and Reason.READER_MISSING not in d.reasons
    assert d.status is Status.ESCALATE


def test_lead_abstain_does_not_fire_split() -> None:
    # grok's 2 reps disagree → grok abstains; must NOT also report grok!=panel (it didn't vote)
    reps = {"grok": _votes("prose", "lineated"), "gemini-pro": _votes("prose"), "ds-flash-text": _votes("prose")}
    d = agg.decide_line(K(1, 0), reps, gates=G)
    assert Reason.GROK_PANEL_SPLIT not in d.reasons and Reason.CORE_ABSTAIN in d.reasons


def test_reader_missing_routes_to_rerun_not_human() -> None:
    reps = {"grok": _votes("prose"), "gemini-pro": _votes("prose")}  # ds-flash produced nothing
    d1 = agg.decide_line(K(1, 0), reps, gates=G)
    assert Reason.READER_MISSING in d1.reasons and d1.status is Status.ESCALATE
    reps3 = {"grok": _votes("prose", "prose", "prose"), "gemini-pro": _votes("prose", "prose", "prose")}
    d3 = agg.decide_line(K(1, 0), reps3, gates=G)
    assert d3.status is Status.NEEDS_RERUN   # operational gap persists → re-run, not editorial


def test_conf_missing() -> None:
    d = agg.decide_line(K(1, 0), {r: [("prose", None)] for r in G.core}, gates=G)
    assert Reason.CONF_MISSING in d.reasons and not d.accepted


def test_conf_floor_zero_disables_conf_gate() -> None:
    g0 = Gates(conf_floor=0.0)
    d = agg.decide_line(K(1, 0), {r: [("prose", None)] for r in g0.core}, gates=g0)
    assert d.status is Status.ACCEPT and not d.reasons


def test_soft_routes_immediately_over_escalatable() -> None:
    # soft (terminal) wins even when an escalatable low-conf reason is also present and reps remain
    reps = {r: _votes("prose", conf=0.4) for r in G.core}
    d = agg.decide_line(K(1, 0), reps, gates=G, soft=True)
    assert d.status is Status.ROUTE_HUMAN and Reason.SOFT in d.reasons


def test_needs_review_terminal() -> None:
    d = agg.decide_line(K(1, 0), {r: _votes("lineated") for r in G.core}, gates=G, needs_review=True)
    assert d.status is Status.ROUTE_HUMAN and Reason.NEEDS_REVIEW in d.reasons


# ---- Gates validation -------------------------------------------------------------------------

def test_gates_reject_impossible_agreement() -> None:
    for bad in ({"min_core_agree": 4}, {"min_core_agree": 0}, {"lead": "nobody"}, {"conf_floor": 1.5}):
        try:
            Gates(**bad)
        except ValueError:
            continue
        raise AssertionError(f"Gates({bad}) should have raised")


# ---- blocks -----------------------------------------------------------------------------------

def test_blocks() -> None:
    keys = [K(0, 0), K(1, 0), K(2, 0), K(3, 0)]
    labels = {K(0, 0): "prose", K(1, 0): "prose", K(2, 0): "lineated", K(3, 0): "lineated"}
    bl = blocks.reconstruct(keys, labels)
    assert [b.label for b in bl] == ["prose", "lineated"] and [len(b.keys) for b in bl] == [2, 2]
    assert blocks.boundaries(keys, labels) == {2}
    assert blocks.boundary_f1(keys, labels, labels) == 1.0
    assert blocks.exact_block_match(keys, labels, labels) == 1.0
    shifted = {K(0, 0): "prose", K(1, 0): "lineated", K(2, 0): "lineated", K(3, 0): "lineated"}
    assert blocks.boundary_f1(keys, shifted, labels) == 0.0
    assert blocks.exact_block_match(keys, shifted, labels) == 0.0
    flat = {k: "prose" for k in keys}
    assert blocks.boundary_f1(keys, flat, flat) == 1.0


def test_blocks_edge_cases() -> None:
    assert blocks.reconstruct([], {}) == []
    assert blocks.boundary_f1([], {}, {}) == 1.0          # no flips on either side
    assert blocks.exact_block_match([], {}, {}) == 1.0
    one = [K(5, 0)]
    assert [b.label for b in blocks.reconstruct(one, {K(5, 0): "prose"})] == ["prose"]
    # an unlabeled mid-region gap breaks the run and counts as two boundaries
    keys = [K(0, 0), K(1, 0), K(2, 0)]
    gapped = {K(0, 0): "prose", K(2, 0): "prose"}          # K(1,0) unlabeled
    assert [len(b.keys) for b in blocks.reconstruct(keys, gapped)] == [1, 1]
    assert blocks.boundaries(keys, gapped) == {1, 2}


# ---- audit ------------------------------------------------------------------------------------

def _audit_lines() -> list[AuditLine]:
    out = []
    for i in range(30):
        rid = "g00_b01" if i < 15 else "g01_b02"
        stratum = "alpha" if i < 15 else "beta"
        out.append(AuditLine(rid, K(i, 0), "prose" if i % 3 else "lineated", stratum))
    return out


def test_audit_determinism_and_coverage() -> None:
    lines = _audit_lines()
    s1 = audit.sample_accepted(lines, rate=0.2, seed=7)
    assert s1 == audit.sample_accepted(lines, rate=0.2, seed=7) and s1     # deterministic, non-empty
    assert audit.sample_accepted(lines, rate=0.2, seed=8) != s1            # seed matters
    assert {ln.stratum for ln in s1} == {"alpha", "beta"}                  # every stratum audited


def test_audit_distinct_books_no_collision() -> None:
    # same (idx,sub) in two books must remain distinct lines
    lines = [AuditLine("g00_b01", K(7, 0), "prose", "alpha"),
             AuditLine("g01_b02", K(7, 0), "lineated", "beta")]
    sample = audit.sample_accepted(lines, rate=1.0, seed=1)
    assert {(s.rid, s.key) for s in sample} == {("g00_b01", K(7, 0)), ("g01_b02", K(7, 0))}


def test_audit_report_error_rate() -> None:
    lines = [AuditLine("g00_b01", K(0, 0), "prose", "alpha"),
             AuditLine("g00_b01", K(1, 0), "prose", "alpha")]
    human = {("g00_b01", 0, 0): "prose", ("g00_b01", 1, 0): "lineated"}  # second is a panel error
    rep = audit.audit_report(lines, human)
    assert rep["alpha"]["error_rate"] == 0.5


def test_normalize_label_legacy_alias() -> None:
    assert normalize_label("flowing") == "prose" and normalize_label("Lineated") == "lineated"
    try:
        normalize_label("verse")
    except ValueError:
        return
    raise AssertionError("normalize_label('verse') should raise")


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
