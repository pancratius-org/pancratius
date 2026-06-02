# research-only: non-Claude vision readers (Grok / Gemini) via OpenRouter for the panel.
"""Add model-diverse readers to the lineation gold panel. The Claude readers (Sonnet/Opus)
can share a bias; an external vision model that never saw our brief's framing is the
strongest guard against panel circularity (review finding H1). Each model sees the SAME
composite image (docx page | prose candidate | lineated candidate) + the per-line structure
and labels every body line prose|lineated — exactly the Claude readers' task.

Reads docs/scratchpad/intent-classifier/data/gold_block/{reader_pkg.json, reader_brief.txt};
writes reader_<tag>.jsonl ({rid, idx, sub, label, conf}). Never prints the API key.

Run: uv run --with python-dotenv --with requests --with pillow python openrouter_reader.py <grok|gemini>
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import NamedTuple

import dotenv
import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from ir_view import LineKey  # noqa: E402

DATA_ROOT = HERE.parent / "data"
API = "https://openrouter.ai/api/v1/chat/completions"
MODELS = {"grok": "x-ai/grok-4.3", "gemini": "google/gemini-3.1-pro-preview",
          "deepseek": "deepseek/deepseek-v4-flash", "owl": "openrouter/owl-alpha",
          "mimo": "xiaomi/mimo-v2.5", "minimax": "minimax/minimax-m3"}
VISION = {"grok", "gemini", "mimo", "minimax"}  # deepseek/owl are text-only → structure, no image


def _api_key() -> str:
    """Resolve OPENROUTER_API_KEY (repo-root .env first, then cwd .env). Read at call time,
    not import time, so importing this module never raises on a missing key."""
    dotenv.load_dotenv(HERE.parents[3] / ".env")
    if not os.environ.get("OPENROUTER_API_KEY"):
        dotenv.load_dotenv()
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit("OPENROUTER_API_KEY not set (checked repo-root .env and cwd .env)")
    return key


def _img_data_url(path: str, max_w: int = 2600) -> str:
    # The composite is ~3 columns; 2600px keeps Cyrillic body text ~870px/column (legible)
    # while bounding the payload.
    from PIL import Image
    im = Image.open(path).convert("RGB")
    if im.width > max_w:
        im = im.resize((max_w, round(im.height * max_w / im.width)))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


class Reply(NamedTuple):
    content: str
    finish_reason: str | None   # "length" ⇒ the model's output was truncated


def post(key: str, model: str, content: list, max_tokens: int = 8192) -> Reply:
    payload = {"model": model, "temperature": 0, "max_tokens": max_tokens,
               "messages": [{"role": "user", "content": content}]}
    h = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    last = ""
    for attempt in range(5):
        try:
            r = requests.post(API, headers=h, json=payload, timeout=180)
            if r.status_code == 200:
                ch = r.json().get("choices") or []
                c = ch[0].get("message", {}).get("content") if ch else None
                if c:
                    return Reply(c, ch[0].get("finish_reason"))
                last = f"empty choices: {r.text[:160]}"
            elif 400 <= r.status_code < 500 and r.status_code != 429:
                # client error other than rate-limit (auth, bad request) — not retryable
                raise RuntimeError(f"{model}: {r.status_code}: {r.text[:160]}")
            else:
                last = f"{r.status_code}: {r.text[:160]}"
        except (requests.RequestException, json.JSONDecodeError) as e:
            last = f"{type(e).__name__}: {e}"
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{model}: {last}")


def _parse_labels(txt: str) -> list[dict]:
    """Pull the labels array out of the model's reply, tolerating fences, a leading reasoning
    array, or nested arrays. Scan all balanced top-level [...] arrays; among those that parse
    to a non-empty list of dicts, prefer the LAST whose objects carry idx+label (the answer
    usually follows any reasoning); fall back to the largest dict-array."""
    cands: list[str] = []
    depth, start = 0, None
    for i, ch in enumerate(txt):
        if ch == "[":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "]" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                cands.append(txt[start:i + 1])
                start = None
    parsed: list[list[dict]] = []
    for c in cands:
        try:
            v = json.loads(c)
        except json.JSONDecodeError:
            continue
        if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
            parsed.append(v)
    labelish = [v for v in parsed if all("idx" in x and "label" in x for x in v)]
    if labelish:
        return labelish[-1]
    return max(parsed, key=len, default=[])


def main(tag: str, data: Path) -> int:
    key = _api_key()
    model = MODELS[tag]
    brief = (data / "reader_brief.txt").read_text()
    pkg = json.loads((data / "reader_pkg.json").read_text())
    out: list[dict] = []
    total_missing = 0
    vision = tag in VISION
    for r in pkg:
        keys = {LineKey(*k) for k in r["keys"]}
        evidence = ("The image shows three columns: the DOCX PAGE (authority), then PROSE and "
                    "LINEATED candidate renderings. Use it with the per-line structure below."
                    if vision else
                    "You are given ONLY the per-line text structure below (no image): the text, "
                    "whether each line WRAPS at the reading column, inline emphasis, and the hard "
                    "structural markers. Judge prose vs lineated from this.")
        ask = (f"{brief}\n\n=== REGION {r['rid']} ===\n{evidence}\n\nStructure (label ONLY these "
               f"body lines):\n\n{r['structure']}\n\n"
               f"Return ONLY a JSON array, one object per body line listed above:\n"
               f'[{{"idx": <int>, "sub": <int>, "label": "prose"|"lineated", "conf": <0..1>}}]\n'
               f"Label every body line. No prose, no code fence — just the JSON array.")
        content = [{"type": "text", "text": ask}]
        try:
            if vision:   # inside the try: a missing/oversized composite must skip the region, not abort
                content.append({"type": "image_url", "image_url": {"url": _img_data_url(r["composite"])}})
            reply = post(key, model, content)
            if reply.finish_reason == "length":   # truncated → one retry at a bigger budget
                reply = post(key, model, content, max_tokens=16384)
        except (RuntimeError, OSError) as e:
            # One region that exhausts retries (outage, oversized payload) must NOT abort the
            # whole model run and discard every prior region's votes — record it as missing
            # (the merge's coverage floor handles absent votes) and continue.
            print(f"  {r['rid']}: !! FAILED — {str(e)[:110]} (0 votes; treated as missing)")
            total_missing += len(keys)
            continue
        raw = data / "raw"
        raw.mkdir(exist_ok=True)
        (raw / f"{tag}_{r['rid']}.txt").write_text(reply.content)
        got: set[LineKey] = set()
        for o in _parse_labels(reply.content):
            try:
                k = LineKey(int(o["idx"]), int(o["sub"]))
                lab = o["label"]
            except (KeyError, TypeError, ValueError):
                continue
            if k in keys and lab in ("prose", "lineated") and k not in got:
                got.add(k)
                out.append({"rid": r["rid"], "idx": k.idx, "sub": k.sub, "label": lab,
                            "conf": float(o.get("conf", 0.5))})
        miss = len(keys) - len(got)
        total_missing += miss
        # A truncated reply or a big coverage gap voids a region's votes and skews consensus
        # toward the models that finished — warn loudly so it's never silent.
        if reply.finish_reason == "length":
            flag = "  !! TRUNCATED (finish_reason=length) — region under-covered"
        elif miss > max(2, len(keys) // 5):
            flag = f"  !! INCOMPLETE — only {len(got)}/{len(keys)} parsed"
        else:
            flag = f"  (missing {miss})" if miss else ""
        print(f"  {r['rid']}: {len(got)}/{len(keys)} labeled{flag}")
    (data / f"reader_{tag}.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in out) + "\n")
    bal = Counter(x["label"] for x in out)
    print(f"{tag} ({model}): {len(out)} lines labeled, missing={total_missing}, balance={dict(bal)}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="add a non-Claude OpenRouter reader to the panel")
    p.add_argument("tag", choices=sorted(MODELS), help="which model to run as a reader")
    p.add_argument("--set", dest="dataset", required=True,
                   help="data subdir, REQUIRED so a run can't default into the pilot "
                        "(pilot=gold_block; scale=gold_block2)")
    args = p.parse_args()
    raise SystemExit(main(args.tag, DATA_ROOT / args.dataset))
