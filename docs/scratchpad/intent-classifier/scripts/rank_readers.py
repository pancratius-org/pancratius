# research-only: rank panel readers by closeness to ground truth (human + consensus).
"""Truth = the human's adjudicated lines (independent, page-grounded) + the consensus gold on
the rest. CONTESTED-acc (the human-resolved set: real splits + the blind audit lines) is the
cleaner discriminator; OVERALL-acc and lineated P/R are over consensus+human and so are partly
circular (consensus was formed from these same readers) — read them together, not in isolation."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Literal, NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ir_view import LineKey

DATA_ROOT = Path(__file__).resolve().parents[1] / "data"
ADJ = Path(__file__).resolve().parents[1] / "adjudicate"

type Label = Literal["prose", "lineated"]


class TruthKey(NamedTuple):
    rid: str
    idx: int
    sub: int


class ReaderScore(NamedTuple):
    """One reader's standing against truth. The clean discriminator is `contested_acc`
    (the human resolved exactly where readers diverged); `overall_acc` is secondary and
    partly circular (consensus was formed from these readers)."""

    tag: str
    contested_acc: float
    n_contested: int
    overall_acc: float
    n_overall: int
    lineated_precision: float
    lineated_recall: float


def _latest_responses(adj: Path, dataset: str, explicit: str | None) -> dict:
    """The adjudication responses: an explicit file if given, else the most recently modified
    responses*.json (preferring ones naming this dataset). Errors clearly if none exist."""
    if explicit:
        return json.loads(Path(explicit).read_text())["responses"]
    by_mtime = lambda p: p.stat().st_mtime  # noqa: E731
    files = (sorted(adj.glob(f"responses*{dataset}*.json"), key=by_mtime)
             or sorted(adj.glob("responses*.json"), key=by_mtime))
    if not files:
        raise SystemExit(f"no responses*.json in {adj} — run the adjudication app first")
    return json.loads(files[-1].read_text())["responses"]


def main(dataset: str, tags: list[str], responses: str | None = None) -> int:
    data = DATA_ROOT / dataset
    # truth: consensus gold + human-resolved contested. An "audit_<rid>" response id is a
    # blind re-label of a consensus region — strip the prefix so it keys onto the real rid.
    truth: dict[TruthKey, Label] = {
        TruthKey(g["rid"], g["idx"], g["sub"]): g["label"]
        for g in map(json.loads, (data / "gold_block.jsonl").read_text().splitlines()) if g
    }
    contested: set[TruthKey] = set()
    for rid, v in _latest_responses(ADJ, dataset, responses).items():
        rid = rid.removeprefix("audit_")
        for k, lab in v.get("lines", {}).items():
            line = LineKey(*(int(x) for x in k.split(".")))
            key = TruthKey(rid, line.idx, line.sub)
            truth[key] = lab
            contested.add(key)

    scores: list[ReaderScore] = []
    for t in tags:
        reader: dict[TruthKey, Label] = {
            TruthKey(x["rid"], x["idx"], x["sub"]): x["label"]
            for x in map(json.loads, (data / f"reader_{t}.jsonl").read_text().splitlines()) if x
        }
        # accuracy on the human-resolved contested lines (the clean discriminator)
        cn = [k for k in contested if k in reader]
        c_acc = sum(reader[k] == truth[k] for k in cn) / len(cn) if cn else 0.0
        # overall accuracy + lineated P/R over all labeled keys present in truth
        keys = [k for k in truth if k in reader]
        acc = sum(reader[k] == truth[k] for k in keys) / len(keys)
        tp = sum(reader[k] == "lineated" and truth[k] == "lineated" for k in keys)
        fp = sum(reader[k] == "lineated" and truth[k] == "prose" for k in keys)
        fn = sum(reader[k] == "prose" and truth[k] == "lineated" for k in keys)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        scores.append(ReaderScore(t, c_acc, len(cn), acc, len(keys), prec, rec))

    scores.sort(key=lambda s: (-s.contested_acc, -s.overall_acc))
    print(f"truth: {len(truth)} lines ({len(contested)} human-resolved contested, rest consensus)\n")
    print(f"{'reader':10} {'contested-acc':>13} {'overall-acc':>12} {'lin-P':>7} {'lin-R':>7}")
    for s in scores:
        print(f"{s.tag:10} {s.contested_acc:>11.1%}({s.n_contested}) "
              f"{s.overall_acc:>10.1%}({s.n_overall}) "
              f"{s.lineated_precision:>7.2f} {s.lineated_recall:>7.2f}")
    return 0


_SCALE_TAGS = ["careful", "grok", "gemini", "owl", "deepseek", "mimo", "minimax"]

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--set", dest="dataset", required=True, help="data subdir (e.g. gold_block2)")
    p.add_argument("--tags", nargs="+", default=_SCALE_TAGS, help="panel reader tags to rank")
    p.add_argument("--responses", default=None, help="explicit responses.json (default: latest in adjudicate/)")
    args = p.parse_args()
    raise SystemExit(main(args.dataset, args.tags, args.responses))
