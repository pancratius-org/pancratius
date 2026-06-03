# research-only: frozen model-rotation benchmark on the PROBLEMATIC contested regions.
"""Tries candidate OpenRouter models on the hardest Phase-B regions (regressions + never-solved
+ a few clean gains for contrast), with the SAME prompt/package/truth join as the panel.
Parallel across model x region (ThreadPoolExecutor — the panel's sequential urllib was slow).
Reports per model: coverage, parse failures, latency, token cost, prose-recall, lineated-recall,
balanced accuracy; and per-region panel-vs-model deltas. Does NOT touch the gold or the rebuild.

Run: uv run --with python-dotenv --with requests --with pillow python bench_models.py
(requests for the API, pillow for composite image encoding, python-dotenv for the key.)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import openrouter_reader as orr  # reuse _api_key, _img_data_url, _parse_labels, API  # noqa: E402

DATA = HERE.parent / "data"
ADJ = HERE.parent / "adjudicate"

# Candidate models to evaluate for rotation. {tag: (model_id, vision)} — vision=True sends the
# composite image + structure; vision=False sends the per-line structure only (text-reader path,
# how deepseek/owl run in the panel). deepseek is probed in BOTH modes to see if the image helps.
CANDIDATES = {
    # qwen3/glm/nemotron/ring are TEXT-ONLY LLMs — they reject the composite image, so they
    # are run text-only (structure listing). gemini/step/perceptron are multimodal.
    "qwen3": ("qwen/qwen3-235b-a22b-2507", False),
    "glm": ("z-ai/glm-4.7-flash", False),
    "nemotron": ("nvidia/nemotron-3-super-120b-a12b", False),
    "ring": ("inclusionai/ring-2.6-1t", False),
    "step": ("stepfun/step-3.7-flash", True),
    "perceptron": ("perceptron/perceptron-mk1", True),
    "gemini-lite": ("google/gemini-3.1-flash-lite", True),
    "gemini-flash": ("google/gemini-3.5-flash", True),
    # deepseek both modes (flash = the incumbent text reader; pro = the new tier)
    "ds-flash-text": ("deepseek/deepseek-v4-flash", False),
    "ds-flash-vis": ("deepseek/deepseek-v4-flash", True),
    "ds-pro-text": ("deepseek/deepseek-v4-pro", False),
    "ds-pro-vis": ("deepseek/deepseek-v4-pro", True),
    # incumbent keepers (for the brief-fix A/B): grok/gemini-pro vision, deepseek-flash = ds-flash-text
    "grok": ("x-ai/grok-4.3", True),
    "gemini-pro": ("google/gemini-3.1-pro-preview", True),
}

# The PROBLEMATIC overlap: panel regressions, never-solved, big gains, AND the prose-bearing
# regions (so prose-recall / the costly over-lineation error is actually measurable).
# g23_b17 is HELD OUT (used as a few-shot prose example), so the prose guardrail is g09+g10.
PROBLEM_RIDS = [
    "g00_b64_t2", "g29_b69_t0", "g05_b37", "g18_b60_t3",        # regressions (lineated)
    "g22_b31_t5", "g24_b28", "g31_b13", "g33_b66",              # never solved (lineated, 0/x)
    "g00_b64_t1", "g34_b63", "g27_b67_t2",                      # big gains (lineated, contrast)
    "g09_b16_t2", "g10_b19",                                    # the PROSE guardrail (costly error)
]
# incumbent panel readers to show alongside the candidates (scored from their phaseb labels).
INCUMBENTS = ["grok", "deepseek", "gemini"]


def book_of(rid: str) -> str | None:
    m = re.search(r"_b(\d+)", rid.removeprefix("audit_"))
    return m.group(1) if m else None


def load_truth() -> dict:
    adj = json.loads((ADJ / "responses-lineation-adjudication-gold-block2-contested-lines.json"
                      ).read_text())["responses"]
    truth = {}
    for rid, v in adj.items():
        for k, lab in v.get("lines", {}).items():
            i, s = k.split(".")
            truth[(book_of(rid), int(i), int(s))] = lab
    return truth


def build_ask(brief: str, region: dict, vision: bool) -> str:
    evidence = ("The image shows three columns: the DOCX PAGE (authority), then PROSE and "
                "LINEATED candidate renderings. Use it with the per-line structure below."
                if vision else
                "You are given ONLY the per-line text structure below (no image): the text, "
                "whether each line WRAPS at the reading column, inline emphasis, and the hard "
                "structural markers. Judge prose vs lineated from this.")
    return (f"{brief}\n\n=== REGION {region['rid']} ===\n{evidence}\n\nStructure (label ONLY these "
            f"body lines):\n\n{region['structure']}\n\n"
            f"Return ONLY a JSON array, one object per body line listed above:\n"
            f'[{{"idx": <int>, "sub": <int>, "label": "prose"|"lineated", "conf": <0..1>}}]\n'
            f"Label every body line. No prose, no code fence — just the JSON array.")


def call(key: str, model: str, ask: str, img_url: str | None, max_tokens: int = 8192) -> dict:
    """One request. Returns {content, finish_reason, usage, latency, error}. img_url=None ⇒ text-only.

    Text-only models get the prompt as a PLAIN STRING — some (e.g. qwen3) parse a single-element
    multimodal content-array as empty (prompt_tokens=8, "message incomplete"). Vision needs the array.
    """
    content: str | list = ask if img_url is None else [
        {"type": "text", "text": ask}, {"type": "image_url", "image_url": {"url": img_url}}]
    payload = {"model": model, "temperature": 0, "max_tokens": max_tokens,
               "usage": {"include": True},
               "messages": [{"role": "user", "content": content}]}
    h = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    t0 = time.time()
    last = ""
    for attempt in range(4):
        try:
            r = requests.post(orr.API, headers=h, json=payload, timeout=180)
            if r.status_code == 200:
                j = r.json()
                ch = j.get("choices") or []
                c = ch[0].get("message", {}).get("content") if ch else None
                if c:
                    return {"content": c, "finish_reason": ch[0].get("finish_reason"),
                            "usage": j.get("usage") or {}, "latency": time.time() - t0, "error": None}
                last = f"empty: {r.text[:120]}"
            elif 400 <= r.status_code < 500 and r.status_code != 429:
                return {"content": "", "usage": {}, "latency": time.time() - t0,
                        "error": f"{r.status_code}: {r.text[:120]}"}
            else:
                last = f"{r.status_code}: {r.text[:120]}"
        except (requests.RequestException, json.JSONDecodeError) as e:
            last = f"{type(e).__name__}: {e}"
        time.sleep(2 * (attempt + 1))
    return {"content": "", "usage": {}, "latency": time.time() - t0, "error": last}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", dest="dataset", default="phaseb")
    ap.add_argument("--models", nargs="+", default=sorted(CANDIDATES))
    ap.add_argument("--brief", default=None, help="override brief file (for the brief-fix A/B)")
    ap.add_argument("--tag-suffix", default="", help="suffix appended to output reader_<tag><suffix>")
    args = ap.parse_args()
    key = orr._api_key()
    data = DATA / args.dataset
    brief = Path(args.brief).read_text() if args.brief else (data / "reader_brief.txt").read_text()
    pkg = {e["rid"]: e for e in json.loads((data / "reader_pkg.json").read_text())}
    regions = [pkg[r] for r in PROBLEM_RIDS if r in pkg]
    print(f"benchmark: {len(args.models)} models x {len(regions)} problematic regions "
          f"(missing from pkg: {[r for r in PROBLEM_RIDS if r not in pkg]})")
    # pre-encode images once per region
    imgs = {e["rid"]: orr._img_data_url(e["composite"]) for e in regions}

    jobs = [(tag, *CANDIDATES[tag], e) for tag in args.models for e in regions]
    results: dict[str, list[dict]] = {tag: [] for tag in args.models}
    meta: dict[str, dict] = {tag: {"lat": [], "ptok": 0, "ctok": 0, "cost": 0.0,
                                   "fail": 0, "parsefail": 0} for tag in args.models}

    def work(tag: str, model: str, vision: bool, e: dict) -> tuple:
        ask = build_ask(brief, e, vision)
        img = imgs[e["rid"]] if vision else None
        res = call(key, model, ask, img)
        if res.get("finish_reason") == "length" and not res["error"]:
            res = call(key, model, ask, img, max_tokens=16384)   # truncated → bigger budget, one retry
        return tag, vision, e, res

    bdir = data / "bench"
    bdir.mkdir(exist_ok=True)
    raw_log: list[dict] = []   # every call's raw reply + metadata, for audit (Codex: too lossy before)
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = [ex.submit(work, t, m, v, e) for (t, m, v, e) in jobs]
        for fut in as_completed(futs):
            tag, vision, e, res = fut.result()
            raw_log.append({"tag": tag, "rid": e["rid"], "vision": vision,
                            "finish_reason": res.get("finish_reason"), "error": res["error"],
                            "usage": res.get("usage") or {}, "latency": round(res["latency"], 2),
                            "content": res["content"]})
            mt = meta[tag]
            mt["lat"].append(res["latency"])
            u = res.get("usage") or {}
            mt["ptok"] += u.get("prompt_tokens", 0) or 0
            mt["ctok"] += u.get("completion_tokens", 0) or 0
            mt["cost"] += (u.get("cost") or 0.0)
            if res["error"]:
                mt["fail"] += 1
                continue
            labels = orr._parse_labels(res["content"])
            keyset = {tuple(k) for k in e["keys"]}
            got = set()
            for o in labels:
                try:
                    k = (int(o["idx"]), int(o["sub"]))
                    lab = o["label"]
                except (KeyError, TypeError, ValueError):
                    continue
                if k in keyset and lab in ("prose", "lineated") and k not in got:
                    got.add(k)
                    results[tag].append({"rid": e["rid"], "idx": k[0], "sub": k[1], "label": lab})
            if not got:
                mt["parsefail"] += 1

    # persist labels + the full raw log (replies, finish_reason, errors, usage, latency)
    for tag in args.models:
        (bdir / f"reader_{tag}{args.tag_suffix}.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in results[tag]) + "\n")
    with (bdir / "raw_replies.jsonl").open("a") as fh:   # APPEND — never erase prior audit metadata
        for r in raw_log:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    # score
    truth = load_truth()
    tkeys = [(book_of(e["rid"]), k[0], k[1]) for e in regions for k in e["keys"]]
    tkeys = [k for k in tkeys if k in truth]
    n_prose = sum(truth[k] == "prose" for k in tkeys)
    n_lin = sum(truth[k] == "lineated" for k in tkeys)
    print(f"\nproblematic truth lines: {len(tkeys)} ({n_prose} prose, {n_lin} lineated)\n")
    all_p = [k for k in tkeys if truth[k] == "prose"]
    all_l = [k for k in tkeys if truth[k] == "lineated"]

    def metrics(lab: dict) -> tuple[float, float, float, float]:
        # recall denominators are ALL class keys (a MISSING label counts as wrong), so a model
        # cannot look good by skipping hard/prose regions (Codex). Coverage is also reported.
        cov = len([k for k in tkeys if k in lab]) / len(tkeys) if tkeys else 0.0
        pr = sum(lab.get(k) == "prose" for k in all_p) / len(all_p) if all_p else float("nan")
        lr = sum(lab.get(k) == "lineated" for k in all_l) / len(all_l) if all_l else float("nan")
        bal = (pr + lr) / 2
        return cov, pr, lr, bal

    print(f"{'model':12} {'cov':>5} {'pfail':>5} {'lat(s)':>7} {'ptok':>7} {'ctok':>6} "
          f"{'cost$':>7} | {'prose-r':>7} {'lin-r':>6} {'bal-acc':>7}")
    for tag in args.models:
        lab = {(book_of(r["rid"]), r["idx"], r["sub"]): r["label"] for r in results[tag]}
        cov, pr, lr, bal = metrics(lab)
        mt = meta[tag]
        avlat = sum(mt["lat"]) / len(mt["lat"]) if mt["lat"] else 0
        print(f"{tag:12} {cov:>4.0%} {mt['parsefail']:>3}/{mt['fail']:<1} {avlat:>7.1f} "
              f"{mt['ptok']:>7} {mt['ctok']:>6} {mt['cost']:>7.4f} | "
              f"{pr:>6.0%} {lr:>5.0%} {bal:>6.0%}")
    print(f"\n-- incumbents (scored from {args.dataset} labels, same subset) --")
    for tag in INCUMBENTS:
        f = data / f"reader_{tag}.jsonl"
        if not f.exists():
            continue
        lab = {}
        for line in f.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                lab[(book_of(r["rid"]), r["idx"], r["sub"])] = r["label"]
        cov, pr, lr, bal = metrics(lab)
        print(f"{tag:12} {cov:>4.0%} {'   -':>5} {'-':>7} {'-':>7} {'-':>6} {'-':>7} | "
              f"{pr:>6.0%} {lr:>5.0%} {bal:>6.0%}")
    print("\n(pfail = parse-failures/api-failures; cost from OpenRouter usage when reported)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
