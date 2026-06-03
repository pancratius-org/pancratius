# research-only: the bridge CLI — load packaged regions + panel reps, gate, route, score, manifest.
"""The only gold-core module that touches disk. Subcommands:

  aggregate  regions + reader reps → gate → <run>/{gold.jsonl, needs_human, escalate, needs_rerun, manifest}
  score      reader reps vs a human truth file → per-reader + panel-decision balanced accuracy
  audit      sample a run's accepted gold lines for human spot-check → <run>/audit_sample.json
  manifest   (re)write the reproducibility contract for a run → <run>/manifest.json

Outputs are RUN-SCOPED under data/<set>/gold/<run_id>/ so a re-run never overwrites a prior one.
Reps live as data/<set>/bench/reader_<reader>_<prefix><rep>.jsonl (one file per rep), rows
{rid, idx, sub, label, conf?}. Regions live as data/<set>/reader_pkg.json.

Run: uv run python -m gold.run aggregate --set phaseb --prefix w --run-id v5-3rep
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path

from . import audit as audit_mod
from . import registry
from .aggregate import decide_region, panel_majority, reader_verdict
from .manifest import Manifest, build_manifest, sha256_file, sha256_text
from .types import (
    AuditLine,
    Gates,
    Label,
    LineKey,
    ReaderId,
    Reason,
    Status,
    Vote,
    normalize_label,
)

HERE = Path(__file__).resolve().parent
DATA = HERE.parents[1] / "data"
REPO = HERE.parents[4]                         # …/scripts/gold → repo root (pancratius/)
BOOKS = REPO / "src" / "content" / "books"


def book_of(rid: str) -> str:
    m = re.search(r"_b(\d+)", rid.removeprefix("audit_"))
    return m.group(1) if m else "??"


def docx_for(book: str) -> Path | None:
    """The source DOCX for a book number, for the manifest digest. Russian is the source corpus."""
    for d in sorted(BOOKS.glob(f"{book}-*")):
        for cand in (d / "ru.docx", d / "en.docx"):
            if cand.exists():
                return cand
        if found := sorted(d.glob("*.docx")):
            return found[0]
    return None


def run_dir(dataset: str, run_id: str) -> Path:
    return DATA / dataset / "gold" / run_id


def load_regions(dataset: str) -> list[dict]:
    return json.loads((DATA / dataset / "reader_pkg.json").read_text())


def rep_files(dataset: str, reader: str, prefix: str) -> list[Path]:
    return sorted((DATA / dataset / "bench").glob(f"reader_{reader}_{prefix}*.jsonl"))


def load_reps(dataset: str, reader: str, prefix: str) -> list[dict[tuple, Vote]]:
    """One dict per rep file (reader_<reader>_<prefix>*.jsonl): (rid, idx, sub) → (label, conf).

    HARD-validates each row: label is canonical (`normalize_label`, accepts `flowing`), conf is
    None or in [0,1], and no (rid,idx,sub) repeats within a rep file (a duplicate would silently
    overwrite a vote). A bad row aborts — never silently coerced."""
    reps: list[dict[tuple, Vote]] = []
    for p in rep_files(dataset, reader, prefix):
        rep: dict[tuple, Vote] = {}
        for n, line in enumerate(p.read_text().splitlines(), 1):
            if not line.strip():
                continue
            r = json.loads(line)
            try:
                addr = (r["rid"], int(r["idx"]), int(r["sub"]))
                label = normalize_label(r["label"])
            except (KeyError, ValueError, TypeError) as e:
                raise SystemExit(f"{p.name}:{n}: bad reader row ({e}): {line[:120]}") from e
            conf = r.get("conf")
            if conf is not None and not (isinstance(conf, int | float) and 0.0 <= conf <= 1.0):
                raise SystemExit(f"{p.name}:{n}: conf out of [0,1]: {conf!r}")
            if addr in rep:
                raise SystemExit(f"{p.name}:{n}: duplicate line {addr} — one vote per line per rep")
            rep[addr] = (label, conf)
        reps.append(rep)
    return reps


def all_reps(dataset: str, readers: Sequence[str], prefix: str) -> dict[ReaderId, list[dict[tuple, Vote]]]:
    return {r: load_reps(dataset, r, prefix) for r in readers}


def reps_digest(dataset: str, readers: Sequence[str], prefix: str) -> str | None:
    """Combined sha256 of the EXACT rep files this run consumes — the precise reproducibility
    anchor for the gold decisions (run-specific, unlike the shared append-only raw log)."""
    parts = [f"{p.name}:{sha256_file(p)}"
             for r in readers for p in rep_files(dataset, r, prefix)]
    return sha256_text("\n".join(sorted(parts))) if parts else None


def write_run_raw(dataset: str, readers: Sequence[str], prefix: str, dest: Path) -> Path | None:
    """Filter the shared bench/raw_replies.jsonl down to THIS run's rows (core readers, this prefix)
    and write a per-run copy, so the manifest hashes only this run's raw output."""
    src = DATA / dataset / "bench" / "raw_replies.jsonl"
    if not src.exists():
        return None
    core = set(readers)
    rows = []
    for line in src.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        run = str(row.get("run", "")).lstrip("_")
        if row.get("tag") in core and (run.startswith(prefix) if prefix else True):
            rows.append(line)
    if not rows:
        return None
    dest.write_text("\n".join(rows) + "\n")
    return dest


def votes_for(region: dict, key: LineKey, reps: Mapping[ReaderId, list[dict[tuple, Vote]]]) -> dict[ReaderId, list[Vote]]:
    addr = (region["rid"], key.idx, key.sub)
    return {r: [rep[addr] for rep in rl if addr in rep] for r, rl in reps.items()}


def _write_manifest(args: argparse.Namespace, gates: Gates, rids: Sequence[str], out: Path) -> None:
    brief = Path(args.brief) if getattr(args, "brief", None) else _default_brief(args.dataset)
    books = {book_of(r) for r in rids}
    raw = write_run_raw(args.dataset, gates.core, args.prefix, out / "raw_replies.jsonl")
    m = build_manifest(
        run_id=args.run_id, repo=REPO, brief=brief, models=registry.model_ids(gates.core),
        docx_paths={b: p for b in sorted(books) if (p := docx_for(b))},
        gates=gates, seed=getattr(args, "seed", 0), sample_rids=rids,
        reps_sha256=reps_digest(args.dataset, gates.core, args.prefix), raw_replies=raw,
    )
    m.write(out / "manifest.json")
    if m.git_dirty:
        print("  !! worktree DIRTY at run time — git_sha alone does not reproduce this run.")
    miss = [b for b in sorted(books) if b not in m.docx_digests]
    if miss:
        print(f"  !! no DOCX digest for books {miss} — manifest provenance incomplete.")


def _default_brief(dataset: str) -> Path:
    v5 = DATA / dataset / "reader_brief_v5.txt"
    return v5 if v5.exists() else (DATA / dataset / "reader_brief.txt")


# ---- aggregate -------------------------------------------------------------------------------

# the gate's terminal sinks, each its own downstream queue (no mixing human + automated work)
_ROUTE_FILES = {Status.ROUTE_HUMAN: "needs_human.json",
                Status.ESCALATE: "escalate.json",
                Status.NEEDS_RERUN: "needs_rerun.json"}


def _scope(regions: list[dict], rids: Sequence[str] | None) -> list[dict]:
    """Restrict to a requested rid subset (sample-manifest scope); abort on an unknown rid."""
    if not rids:
        return regions
    by_rid = {e["rid"]: e for e in regions}
    if missing := [r for r in rids if r not in by_rid]:
        raise SystemExit(f"--rids not in reader_pkg.json: {missing}")
    return [by_rid[r] for r in rids]


def cmd_aggregate(args: argparse.Namespace) -> int:
    gates = _gates(args)
    regions = _scope(load_regions(args.dataset), args.rids)
    reps = all_reps(args.dataset, gates.core, args.prefix)
    soft = _load_keyset(args.soft)
    needs_review = _load_keyset(args.needs_review)
    out = run_dir(args.dataset, args.run_id)

    # overwrite guard — a re-used run id must not silently clobber a prior run's results
    if (out / "gold.jsonl").exists() and not args.force:
        raise SystemExit(f"run '{args.run_id}' already exists at {out} — use --force to overwrite")

    # batch-coverage guard — a region with NO reps at this prefix is out-of-batch (e.g. --prefix w
    # over a `pw` region), and would be silently scored all-missing.
    uncovered = [region["rid"] for region in regions
                 if not any((region["rid"], k[0], k[1]) in rep
                            for r in gates.core for rep in reps[r] for k in region["keys"])]
    if uncovered and not args.force:
        raise SystemExit(f"{len(uncovered)} region(s) have no '{args.prefix}' reps (out-of-batch): "
                         f"{uncovered[:8]} — scope with --rids, fix --prefix, or --force.")

    gold: list[dict] = []
    routes: dict[Status, dict[str, dict]] = {s: {} for s in _ROUTE_FILES}
    status_count: Counter = Counter()
    reason_count: Counter = Counter()
    by_stratum: dict[str, Counter] = defaultdict(Counter)

    for region in regions:
        rid = region["rid"]
        keys = [LineKey(*k) for k in region["keys"]]
        reps_by_line = {k: votes_for(region, k, reps) for k in keys}
        decisions = decide_region(
            keys, reps_by_line, gates=gates,
            soft=frozenset(k for k in keys if (rid, k) in soft),
            needs_review=frozenset(k for k in keys if (rid, k) in needs_review),
        )
        for d in decisions:
            status_count[d.status] += 1
            reason_count.update(d.reasons)
            by_stratum[region["stratum"]][d.status] += 1
            if d.accepted:
                gold.append({"rid": rid, "idx": d.key.idx, "sub": d.key.sub, "label": d.label,
                             "stratum": region["stratum"]})
            else:
                entry = routes[d.status].setdefault(
                    rid, {"composite": region.get("composite"), "stratum": region["stratum"], "lines": []})
                entry["lines"].append({
                    "idx": d.key.idx, "sub": d.key.sub, "label": d.label,
                    "reasons": [r.value for r in d.reasons],
                    "verdicts": d.verdicts, "lead_conf": d.lead_conf, "rep_count": d.rep_count,
                })

    # conf-missing is an UNSAFE default, not a warning: abort before writing unless explicitly allowed
    n_conf_missing = reason_count.get(Reason.CONF_MISSING, 0)
    if gates.conf_floor > 0 and n_conf_missing and not args.allow_conf_missing:
        raise SystemExit(
            f"ABORT: {n_conf_missing} lines have no per-line confidence but conf_floor="
            f"{gates.conf_floor}. These reps predate conf capture. Re-run readers preserving conf, "
            f"or pass --conf-floor 0 (structure-only gate) or --allow-conf-missing (accept the gap).")

    out.mkdir(parents=True, exist_ok=True)
    (out / "gold.jsonl").write_text("".join(json.dumps(g, ensure_ascii=False) + "\n" for g in gold))
    for st, fname in _ROUTE_FILES.items():
        (out / fname).write_text(json.dumps(routes[st], ensure_ascii=False, indent=2))
    _write_manifest(args, gates, [e["rid"] for e in regions], out)

    total = sum(status_count.values())
    print(f"gold aggregate [{args.dataset} prefix={args.prefix!r} run={args.run_id}] gates={_gates_str(gates)}")
    print(f"  {total} body lines over {len(regions)} regions")
    for st in Status:
        n = status_count[st]
        print(f"    {st.value:12} {n:4}  ({n / total:.0%})" if total else f"    {st.value:12} {n:4}")
    print(f"  accepted → {len(gold)} gold lines; "
          f"to-human {sum(len(v['lines']) for v in routes[Status.ROUTE_HUMAN].values())} · "
          f"escalate {sum(len(v['lines']) for v in routes[Status.ESCALATE].values())} · "
          f"rerun {sum(len(v['lines']) for v in routes[Status.NEEDS_RERUN].values())}")
    if reason_count:
        print("  non-accept reasons:", ", ".join(f"{r.value}×{n}" for r, n in reason_count.most_common()))
    print("  per-stratum accept rate:")
    for s in sorted(by_stratum):
        c = by_stratum[s]
        tot = sum(c.values())
        print(f"    {s:10} {c[Status.ACCEPT]:3}/{tot:<3} ({c[Status.ACCEPT] / tot:.0%})")
    print(f"  wrote {out}/{{gold.jsonl,needs_human.json,escalate.json,needs_rerun.json,manifest.json}}")
    if gates.conf_floor > 0 and n_conf_missing:
        print(f"  !! {n_conf_missing} conf-missing lines were ALLOWED (--allow-conf-missing).")
    return 0
    return 0


# ---- score -----------------------------------------------------------------------------------

def load_truth(path: Path) -> dict[tuple, Label]:
    d = json.loads(path.read_text())["responses"]
    return {(book_of(rid), int(k.split(".")[0]), int(k.split(".")[1])): lab
            for rid, v in d.items() for k, lab in v.get("lines", {}).items()}


def cmd_score(args: argparse.Namespace) -> int:
    gates = _gates(args)
    truth = load_truth(Path(args.truth))
    regions = {e["rid"]: e for e in load_regions(args.dataset)}
    rids = args.rids or list(regions)
    reps = all_reps(args.dataset, gates.core, args.prefix)

    truth_keys = [(book_of(rid), k[0], k[1]) for rid in rids if rid in regions
                  for k in regions[rid]["keys"]]
    truth_keys = [k for k in truth_keys if k in truth]
    prose = [k for k in truth_keys if truth[k] == "prose"]
    lin = [k for k in truth_keys if truth[k] == "lineated"]
    print(f"score [{args.dataset} prefix={args.prefix!r}] {len(truth_keys)} truth lines "
          f"({len(prose)} prose, {len(lin)} lineated) over {len(rids)} regions\n")

    def recall(labels: Mapping[tuple, Label], want: Label, ks: Sequence[tuple]) -> float:
        return sum(labels.get(k) == want for k in ks) / len(ks) if ks else float("nan")

    def coverage(labels: Mapping[tuple, Label], ks: Sequence[tuple]) -> float:
        return sum(k in labels for k in ks) / len(ks) if ks else float("nan")

    def verdict(region: dict, k: Sequence[int], r: str) -> Label | None:
        return reader_verdict([lab for lab, _ in votes_for(region, LineKey(*k), {r: reps[r]})[r]])

    print(f"  {'reader':14} | prose-rec | lin-rec | bal-acc | coverage")
    for r in gates.core:
        labels = {(book_of(rid), k[0], k[1]): v
                  for rid in rids if rid in regions
                  for k in regions[rid]["keys"]
                  if (v := verdict(regions[rid], k, r)) is not None}
        pr, lr = recall(labels, "prose", prose), recall(labels, "lineated", lin)
        print(f"  {r:14} | {pr:>7.0%}  | {lr:>5.0%}  | {(pr+lr)/2:>5.0%}  | {coverage(labels, truth_keys):>6.0%}")

    # the label the grok-led gate would carry (lead verdict, else panel majority)
    panel: dict[tuple, Label] = {}
    for rid in rids:
        if rid not in regions:
            continue
        for k in regions[rid]["keys"]:
            verds = {r: verdict(regions[rid], k, r) for r in gates.core}
            if (lab := verds.get(gates.lead) or panel_majority(verds, gates)) is not None:
                panel[(book_of(rid), k[0], k[1])] = lab
    pr, lr = recall(panel, "prose", prose), recall(panel, "lineated", lin)
    print(f"  {'GROK-LED':14} | {pr:>7.0%}  | {lr:>5.0%}  | {(pr+lr)/2:>5.0%}  | {coverage(panel, truth_keys):>6.0%}")
    return 0


# ---- audit -----------------------------------------------------------------------------------

def cmd_audit(args: argparse.Namespace) -> int:
    out = run_dir(args.dataset, args.run_id)
    gold_path = out / "gold.jsonl"
    if not gold_path.exists():
        raise SystemExit(f"no gold at {gold_path} — run `aggregate --run-id {args.run_id}` first")
    lines = [
        AuditLine(g["rid"], LineKey(g["idx"], g["sub"]), g["label"], g.get("stratum", "?"))
        for g in (json.loads(x) for x in gold_path.read_text().splitlines() if x.strip())
    ]
    sample = audit_mod.sample_accepted(lines, rate=args.rate, seed=args.seed)
    payload = {"seed": args.seed, "rate": args.rate,
               "lines": [{"rid": s.rid, "idx": s.key.idx, "sub": s.key.sub, "label": s.label,
                          "stratum": s.stratum} for s in sample]}
    (out / "audit_sample.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"audit: sampled {len(sample)}/{len(lines)} accepted lines (rate={args.rate}, seed={args.seed}) "
          f"→ {out/'audit_sample.json'}")
    by_stratum = Counter(s.stratum for s in sample)
    print("  per-stratum:", ", ".join(f"{k}×{v}" for k, v in sorted(by_stratum.items())))
    return 0


# ---- manifest --------------------------------------------------------------------------------

def cmd_manifest(args: argparse.Namespace) -> int:
    gates = _gates(args)
    regions = load_regions(args.dataset)
    out = run_dir(args.dataset, args.run_id)
    out.mkdir(parents=True, exist_ok=True)
    _write_manifest(args, gates, [e["rid"] for e in regions], out)
    mf = Manifest.read(out / "manifest.json")
    print(f"manifest [{args.run_id}] → {out/'manifest.json'}\n"
          f"  git={mf.git_sha} dirty={mf.git_dirty} brief_sha={mf.brief_sha256}\n"
          f"  docx_digests={len(mf.docx_digests)} books; models={mf.models}\n  gates={mf.gates}")
    return 0


# ---- shared ----------------------------------------------------------------------------------

def _gates(args: argparse.Namespace) -> Gates:
    kw: dict = {}
    if getattr(args, "core", None):
        kw["core"] = tuple(args.core)
        kw["lead"] = args.core[0]
    if getattr(args, "conf_floor", None) is not None:
        kw["conf_floor"] = args.conf_floor
    return Gates(**kw)


def _gates_str(g: Gates) -> str:
    return f"core={g.core} lead={g.lead} conf_floor={g.conf_floor} min_agree={g.min_core_agree}"


def _load_keyset(path: str | None) -> set[tuple[str, LineKey]]:
    """Optional flag file: {rid: [[idx,sub], ...]} → {(rid, LineKey)}."""
    if not path:
        return set()
    d = json.loads(Path(path).read_text())
    return {(rid, LineKey(*k)) for rid, ks in d.items() for k in ks}


def main() -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--set", dest="dataset", default="phaseb")
    common.add_argument("--prefix", default="w", help="rep-file prefix (e.g. w=v5, c=baseline)")
    common.add_argument("--core", nargs="+", help="override core readers (first is lead)")
    common.add_argument("--conf-floor", type=float, dest="conf_floor",
                        help="confidence gate (0 disables; default 0.7)")

    ap = argparse.ArgumentParser(prog="gold.run", parents=[common])
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("aggregate", parents=[common], help="gate regions → run-scoped gold + routed queues")
    a.add_argument("--run-id", required=True)
    a.add_argument("--rids", nargs="+", help="scope to a region subset (sample manifest)")
    a.add_argument("--force", action="store_true", help="overwrite an existing run / ignore the batch-coverage guard")
    a.add_argument("--allow-conf-missing", action="store_true", dest="allow_conf_missing",
                   help="accept lines with no per-line confidence (default: abort when conf_floor>0)")
    a.add_argument("--brief", help="brief file to record in the manifest (default: v5)")
    a.add_argument("--seed", type=int, default=0)
    a.add_argument("--soft", help="json {rid:[[idx,sub]]} of prior-dependent lines")
    a.add_argument("--needs-review", dest="needs_review", help="json {rid:[[idx,sub]]} of flagged lines")
    a.set_defaults(func=cmd_aggregate)

    s = sub.add_parser("score", parents=[common], help="eval reps vs a human truth file")
    s.add_argument("--truth", required=True)
    s.add_argument("--rids", nargs="+")
    s.set_defaults(func=cmd_score)

    au = sub.add_parser("audit", parents=[common], help="sample a run's accepted gold for spot-check")
    au.add_argument("--run-id", required=True)
    au.add_argument("--rate", type=float, default=0.08)
    au.add_argument("--seed", type=int, default=0)
    au.set_defaults(func=cmd_audit)

    mf = sub.add_parser("manifest", parents=[common], help="(re)write the reproducibility manifest")
    mf.add_argument("--run-id", required=True)
    mf.add_argument("--brief")
    mf.add_argument("--seed", type=int, default=0)
    mf.set_defaults(func=cmd_manifest)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
