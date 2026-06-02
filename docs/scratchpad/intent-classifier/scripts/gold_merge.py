# research-only: merge reader-panel labels into provisional gold + a needs-human queue.
"""Take N independent readers' per-line {prose|lineated} labels over the packaged regions
and produce:
  gold_block.jsonl   — lines a supermajority (min_agree) of the panel agrees on (provisional
                       gold, pending the human's spot-check). One row per body line.
  needs_human.json   — lines the readers split on or that are too thinly voted, each with
                       every reader's call + the composite image path, for the user to
                       settle as final authority. This is also the difficulty map.

Metrics (diagnostic):
  - per-line: consensus rate, label balance, pairwise agreement / PABAK / Cohen's κ.
  - BLOCK-level (the metric that matches what the reader sees): collapse each reader's
    per-line labels into prose/lineated blocks (bounded by hard markers + label changes)
    and report boundary-F1 + exact-block-match between reader pairs. One misplaced
    boundary = one full error, not 1/N.

A reader file is data/gold_block/reader_<tag>.jsonl, one JSON/line:
  {"rid": "...", "idx": int, "sub": int, "label": "prose"|"lineated", "conf": float}

Run: uv run python gold_merge.py <tag1> <tag2> [tag3 ...]
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Literal, NamedTuple, TypedDict

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ir_view import LineKey

DATA = Path(__file__).resolve().parents[1] / "data" / "gold_block"

# The two structural classes the panel votes. A closed value set with no behavior that
# round-trips through json, so a Literal — not a StrEnum.
type Label = Literal["prose", "lineated"]


class RegionKey(NamedTuple):
    """A line's full address across regions: region id + its LineKey (idx, sub)."""

    rid: str
    idx: int
    sub: int


class ReaderVote(TypedDict):
    """One reader's call for one line, as stored in reader_<tag>.jsonl."""

    rid: str
    idx: int
    sub: int
    label: Label
    conf: float


class Block(NamedTuple):
    """A maximal same-label run of body lines: [start, end] inclusive over LineKeys."""

    start: LineKey
    end: LineKey
    label: Label


class PRF(NamedTuple):
    precision: float
    recall: float
    f1: float


@dataclass
class Tally:
    """A hits-out-of-total counter (replaces the bare `[hits, total]` two-element lists)."""

    hits: int = 0
    total: int = 0

    def observe(self, hit: bool) -> None:
        self.total += 1
        self.hits += hit

    @property
    def rate(self) -> float:
        return self.hits / self.total if self.total else 0.0


def _load_reader(data: Path, tag: str) -> dict[RegionKey, ReaderVote]:
    fp = data / f"reader_{tag}.jsonl"
    out: dict[RegionKey, ReaderVote] = {}
    if not fp.exists():
        print(f"  ! missing {fp.name}")
        return out
    for line in fp.read_text().splitlines():
        if not line.strip():
            continue
        r: ReaderVote = json.loads(line)
        out[RegionKey(r["rid"], r["idx"], r["sub"])] = r
    return out


def _blocks(labels: dict[LineKey, Label], keys: list[LineKey]) -> list[Block]:
    """Collapse a region's per-LineKey labels into contiguous same-label blocks over the
    ordered body keys. Boundaries fall where the label changes between adjacent body lines."""
    blocks: list[Block] = []
    cur_label: Label | None = None
    start: LineKey | None = None
    prev: LineKey | None = None
    for k in keys:
        lab = labels.get(k)
        if lab is None:
            continue
        if lab != cur_label:
            if cur_label is not None:
                assert start is not None and prev is not None
                blocks.append(Block(start, prev, cur_label))
            cur_label, start = lab, k
        prev = k
    if cur_label is not None:
        assert start is not None and prev is not None
        blocks.append(Block(start, prev, cur_label))
    return blocks


def _boundary_f1(a_keys: set[LineKey], b_keys: set[LineKey]) -> PRF:
    """Boundary sets = the start-keys of each block after the first. F1 of A vs B."""
    if not a_keys and not b_keys:
        return PRF(1.0, 1.0, 1.0)
    tp = len(a_keys & b_keys)
    prec = tp / len(b_keys) if b_keys else (1.0 if not a_keys else 0.0)
    rec = tp / len(a_keys) if a_keys else (1.0 if not b_keys else 0.0)
    f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0
    return PRF(prec, rec, f1)


def main(tags: list[str], data: Path = DATA, min_agree: int | None = None) -> int:
    pkg = {r["rid"]: r for r in json.loads((data / "reader_pkg.json").read_text())}
    readers = {t: _load_reader(data, t) for t in tags}
    readers = {t: d for t, d in readers.items() if d}
    if not readers:
        print("no reader files found")
        return 1
    tags = list(readers)
    # min_agree = how many readers must share the majority label for a line to be gold; it is
    # ALSO the coverage floor (a line needs ≥ min_agree votes — fewer can't reach agreement,
    # so a lone surviving vote never becomes gold). Default = unanimity; pass lower (e.g. 7 of
    # 9) for a decisive supermajority. Floored at a true majority.
    if min_agree is None:
        min_agree = len(tags)
    min_agree = max(len(tags) // 2 + 1, min(min_agree, len(tags)))
    print(f"merging {len(tags)} readers: {tags} (min_agree={min_agree}; also the coverage floor)\n")

    gold: list[dict] = []
    needs: list[dict] = []
    nongold_rids: set[str] = set()
    per_stratum: defaultdict[str, Tally] = defaultdict(Tally)  # gold vs total per stratum
    label_counts: Counter[Label] = Counter()
    # for κ / agreement
    pair_agree = {p: Tally() for p in combinations(tags, 2)}
    confusion: dict[tuple[str, str], Counter] = {p: Counter() for p in combinations(tags, 2)}

    for rid, info in pkg.items():
        keys = [LineKey(*k) for k in info["keys"]]
        for idx, sub in keys:
            votes = {t: v for t in tags if (v := readers[t].get(RegionKey(rid, idx, sub)))}
            if not votes:
                continue
            labs = [v["label"] for v in votes.values()]
            per_stratum[info["stratum"]].total += 1  # gold (hits) flips in the gold branch
            for a, b in combinations(tags, 2):
                if a in votes and b in votes:
                    la, lb = votes[a]["label"], votes[b]["label"]
                    pair_agree[(a, b)].observe(la == lb)
                    confusion[(a, b)][(la, lb)] += 1
            votes_view = {"votes": {t: {"label": votes[t]["label"], "conf": votes[t].get("conf")} for t in votes}}
            cnt = Counter(labs)
            (top_label, top_n), *rest = cnt.most_common()
            tie = bool(rest) and rest[0][1] == top_n           # 3-2 etc. — no majority
            margin = f"{top_n}-{len(labs) - top_n}"
            if len(votes) < min_agree:                          # can't reach min_agree → human
                nongold_rids.add(rid)
                needs.append({"book": info["book"], "rid": rid, "idx": idx, "sub": sub,
                              "stratum": info["stratum"], "composite": info["composite"],
                              "reason": "thin-votes", "margin": margin, **votes_view})
            elif top_n >= min_agree and not tie:                # decisive (super)majority → gold
                per_stratum[info["stratum"]].hits += 1
                label_counts[top_label] += 1
                g = {"book": info["book"], "rid": rid, "idx": idx, "sub": sub,
                     "label": top_label, "stratum": info["stratum"], "margin": margin,
                     "conf": round(sum(v.get("conf", 1.0) for v in votes.values()) / len(votes), 2)}
                if top_n < len(labs):                           # record the dissenter(s)
                    g["dissent"] = {t: votes[t]["label"] for t in votes if votes[t]["label"] != top_label}
                gold.append(g)
            else:                                               # tie / below threshold → human
                nongold_rids.add(rid)
                needs.append({"book": info["book"], "rid": rid, "idx": idx, "sub": sub,
                              "stratum": info["stratum"], "composite": info["composite"],
                              "reason": "split", "margin": margin, **votes_view})

    (data / "needs_human.json").write_text(json.dumps(needs, ensure_ascii=False, indent=1))

    tot = len(gold) + len(needs)
    print(f"per-line: {len(gold)}/{tot} consensus gold (>={min_agree}/{len(tags)} agree, {len(gold)/tot:.1%}), "
          f"{len(needs)} split → needs_human")
    print(f"  gold label balance: {dict(label_counts)}")
    print("  consensus-gold rate by stratum:")
    for s, ts in sorted(per_stratum.items()):
        print(f"    {s:<12} {ts.hits}/{ts.total} = {ts.rate:.1%}" if ts.total else f"    {s}: 0")
    print("  pairwise reader agreement / κ:")
    for (a, b), ts in pair_agree.items():
        if not ts.total:
            continue
        c = confusion[(a, b)]
        # Cohen κ on prose/lineated
        po = ts.rate
        na: dict[Label, int] = {"prose": 0, "lineated": 0}
        nb: dict[Label, int] = {"prose": 0, "lineated": 0}
        for (la, lb), n in c.items():
            na[la] += n
            nb[lb] += n
        pe = sum((na[lab] / ts.total) * (nb[lab] / ts.total) for lab in na)
        # κ is uninterpretable under label skew (the prevalence/κ paradox: 19/20 agree → κ≈0).
        # PABAK (2·po−1) is stable under skew, so report it as the primary number; flag κ
        # degenerate when chance agreement is near-total.
        pabak = 2 * po - 1
        kstr = "n/a(degenerate)" if pe >= 0.99 else f"{(po - pe) / (1 - pe):.3f}"
        print(f"    {a}~{b}: agree={po:.3f} PABAK={pabak:.3f} κ={kstr}  conf={dict(c)}")

    # Block-level metrics between reader pairs. exact-block-match = fraction of regions whose
    # two partitions are identical. boundary-F1 is computed ONLY over boundary-bearing regions
    # (an all-prose region has no internal boundary and would score a vacuous 1.0); the count
    # of such regions is printed so the denominator is honest.
    print("  block-level (reader vs reader):")
    for a, b in combinations(tags, 2):
        f1s: list[float] = []
        exact = nreg = 0
        for rid, info in pkg.items():
            keys = [LineKey(*k) for k in info["keys"]]
            la = {kk: readers[a][RegionKey(rid, *kk)]["label"]
                  for kk in keys if RegionKey(rid, *kk) in readers[a]}
            lb = {kk: readers[b][RegionKey(rid, *kk)]["label"]
                  for kk in keys if RegionKey(rid, *kk) in readers[b]}
            if not la or not lb:
                continue
            # Partition both readers over the SHARED keys only — a key one reader skipped must
            # not silently bridge a block and fake a disagreement.
            common = [kk for kk in keys if kk in la and kk in lb]
            if not common:
                continue
            nreg += 1
            ba = _blocks(la, common)
            bb = _blocks(lb, common)
            if ba == bb:
                exact += 1
            sa = {blk.start for blk in ba[1:]}
            sb = {blk.start for blk in bb[1:]}
            if sa or sb:                       # boundary-bearing only
                f1s.append(_boundary_f1(sa, sb).f1)
        mf = f"{sum(f1s)/len(f1s):.3f}" if f1s else "n/a"
        print(f"    {a}~{b}: exact-block-match={exact}/{nreg}  "
              f"boundary-F1={mf} over {len(f1s)} boundary-bearing regions")

    # Shared-reader bias never shows up in needs_human (it only holds disagreements). Pull a
    # deterministic audit sample of fully-gold regions (every line reached the supermajority)
    # so the human spot-checks consensus gold too, not just the splits.
    unan_rids = [rid for rid in pkg if rid not in nongold_rids]
    rng = random.Random(20260531)
    rng.shuffle(unan_rids)
    k_audit = max(1, round(0.15 * len(unan_rids)))
    audit = [{"rid": rid, "book": pkg[rid]["book"], "stratum": pkg[rid]["stratum"],
              "composite": pkg[rid]["composite"], "reason": "audit-consensus",
              "agreed": [{"idx": g["idx"], "sub": g["sub"], "label": g["label"]}
                         for g in gold if g["rid"] == rid]}
             for rid in sorted(unan_rids[:k_audit])]
    (data / "audit_queue.json").write_text(json.dumps(audit, ensure_ascii=False, indent=1))

    (data / "gold_block.jsonl").write_text(
        "\n".join(json.dumps(g, ensure_ascii=False) for g in gold) + "\n")
    reasons = Counter(n["reason"] for n in needs)
    print(f"\nwrote gold_block.jsonl ({len(gold)}) + needs_human.json ({len(needs)} lines {dict(reasons)}, "
          f"{len(nongold_rids)} regions) + audit_queue.json ({len(audit)} fully-gold regions to spot-check)")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="merge reader-panel labels into provisional gold")
    p.add_argument("tags", nargs="+", help="reader tags, e.g. careful rhythm opus")
    p.add_argument("--min-agree", type=int, default=None,
                   help="readers that must share the majority label for gold (default: unanimity)")
    p.add_argument("--set", dest="dataset", required=True,
                   help="data subdir, REQUIRED so a run can't default into the pilot "
                        "(pilot=gold_block; scale=gold_block2)")
    args = p.parse_args()
    data = Path(__file__).resolve().parents[1] / "data" / args.dataset
    raise SystemExit(main(args.tags, data, args.min_agree))
