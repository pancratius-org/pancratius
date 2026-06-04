# research-only: build a blind human-adjudication task from a run's routed + audit queues.
"""Turn the gate's human queue (needs_human.json) and the random audit sample (audit_sample.json)
into one `assessment_*.json` for adjudicate.html. The task is BLIND — the DOCX page is the
authority, no panel votes / accepted labels are shown — so audit lines get an unbiased re-label.

A sidecar `review_*.json` records each line's role (audit vs human-split) and, for audit lines, the
gate's accepted label, so ingestion can: split lines → human label is gold; audit lines → compare
to accepted (error rate). One reader-pkg image per region; lines are merged per region.
"""
from __future__ import annotations

import base64
import io
import json
import re
from collections import defaultdict
from pathlib import Path

from .types import LineKey

_LINEOPTS = [{"value": "prose", "label": "Prose"}, {"value": "lineated", "label": "Lineated"}]
# the structure listing emits a body line as "  <idx>.<sub>  <wrap> | <text>"
_LINE = re.compile(r"^\s*(\d+)\.(\d+)\s+\S+\s*\|\s*(.*)$")


def _img_data_uri(path: str, max_w: int = 2200) -> str:
    from PIL import Image
    im = Image.open(path).convert("RGB")
    if im.width > max_w:
        im = im.resize((max_w, round(im.height * max_w / im.width)))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=82)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _texts(structure: str) -> dict[LineKey, str]:
    out: dict[LineKey, str] = {}
    for ln in structure.splitlines():
        if m := _LINE.match(ln):
            out[LineKey(int(m.group(1)), int(m.group(2)))] = m.group(3).strip()
    return out


def build(run_dirs: list[tuple[str, Path]], pkg_of: dict[str, dict]) -> tuple[dict, dict, dict]:
    """run_dirs: (dataset, run_dir) pairs. pkg_of: dataset → {rid: pkg_entry}. Returns (task,
    sidecar, stats). Queued lines (human-split + audit) are merged per region and presented blind."""
    queued: dict[tuple[str, str], dict[tuple[int, int], str]] = defaultdict(dict)
    audit_truth: dict[str, str] = {}
    human_keys: list[str] = []
    for dataset, run in run_dirs:
        for rid, v in json.loads((run / "needs_human.json").read_text()).items():
            for line in v["lines"]:
                queued[(dataset, rid)][(line["idx"], line["sub"])] = "human"
                human_keys.append(f"{rid}|{line['idx']}.{line['sub']}")
        for line in json.loads((run / "audit_sample.json").read_text())["lines"]:
            queued[(dataset, line["rid"])][(line["idx"], line["sub"])] = "audit"
            audit_truth[f"{line['rid']}|{line['idx']}.{line['sub']}"] = line["label"]

    items = []
    for (dataset, rid), keymap in sorted(queued.items()):
        info = pkg_of[dataset][rid]
        texts = _texts(info["structure"])
        lines = [{"key": f"{i}.{s}", "text": texts.get(LineKey(i, s), "?")} for i, s in sorted(keymap)]
        items.append({
            "id": rid, "mode": "per-line", "image": _img_data_uri(info["composite"]),
            "structure": info["structure"], "lineOptions": _LINEOPTS, "lines": lines,
            "hint": f"{len(lines)} line(s) to decide from the page.",
        })
    task = {
        "title": "Lineation gold review — human queue + audit",
        "instructions": ("The DOCX PAGE (left column) is the authority. For each line decide what "
                         "reads TRUE: prose (a flowing paragraph the author merely broke with Enter) "
                         "or lineated (the break is intended — verse / litany / prayer / vow / "
                         "enumerated sequence; joining it would damage the reading). No panel votes "
                         "are shown — decide from the page. Your label is final for these lines."),
        "items": items,
    }
    sidecar = {"audit": audit_truth, "human": sorted(set(human_keys))}
    stats = {"regions": len(items), "lines": sum(len(i["lines"]) for i in items),
             "human": len(set(human_keys)), "audit": len(audit_truth)}
    return task, sidecar, stats
