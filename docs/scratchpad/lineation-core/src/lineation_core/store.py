# research-pure: THE I/O boundary — the only module that knows where annotations + records live.
"""The single read/write edge. Domain code stays pure (it takes data as arguments); every disk
path lives here. The committed annotation TRUTH (`annotations/`) and the derived record CACHE
(`_artifacts/`) are both reached through this module, so a layout change touches one file and a
domain function can never read a path behind the caller's back.

Annotation files are returned as RAW rows (list of dicts); interpreting them into typed records
(reject unmapped, group by reader, …) is pure and lives in `annotations.py`. Record artifacts are
returned as validated `LineRecord`s through the existing hash-railed `artifact` loader."""
from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from . import artifact, paths
from .identity import BookId
from .records import LineRecord, RecordsByBook

LABELS_FILE = "labels.jsonl"   # the resolved per-line truth
VOTES_FILE = "votes.jsonl"     # the LLM panel votes


# --- committed annotation TRUTH (source; never rebuilt) ---------------------------------------

def _rows(path: Path) -> list[dict[str, Any]]:
    """Every jsonl row of a committed annotation file. FAILS LOUD with a clear message if the
    file is missing — it is irreplaceable source truth, not a derived cache to rebuild."""
    if not path.is_file():
        raise FileNotFoundError(
            f"committed annotation file missing: {path} — it is source truth, not rebuilt; "
            f"restore it (see {paths.ANNOTATIONS}).")
    return list(artifact.read_jsonl(path))


def _must(path: Path) -> Path:
    """A committed/derived file that must already exist; FAILS LOUD with its path if not."""
    if not path.is_file():
        raise FileNotFoundError(f"expected file missing: {path}")
    return path


def load_label_rows(*, annotations: Path | None = None) -> list[dict[str, Any]]:
    return _rows((annotations or paths.ANNOTATIONS) / LABELS_FILE)


def load_vote_rows(*, annotations: Path | None = None) -> list[dict[str, Any]]:
    return _rows((annotations or paths.ANNOTATIONS) / VOTES_FILE)


def load_eval_set(name: str, *, annotations: Path | None = None) -> list[dict[str, Any]]:
    """Raw `{id, label}` rows of a named evaluation slice (`eval_sets/<name>.json`) — a committed
    hard-case subpopulation to score the student against. FAILS LOUD if missing."""
    path = (annotations or paths.ANNOTATIONS) / "eval_sets" / f"{name}.json"
    if not path.is_file():
        raise FileNotFoundError(f"committed eval set missing: {path}")
    return json.loads(path.read_text())


def load_selection(name: str, *, annotations: Path | None = None) -> list:
    """A committed `LineId`-key list (`selections/<name>.json`) — e.g. the active-learning acquire
    set, written as DATA by the eval/student side so the teacher consumes it without importing it.
    FAILS LOUD if missing."""
    path = (annotations or paths.ANNOTATIONS) / "selections" / f"{name}.json"
    if not path.is_file():
        raise FileNotFoundError(f"committed selection missing: {path}")
    return json.loads(path.read_text())


# --- derived record CACHE (load-only, hash-railed; built by `build_records`) -------------------

def load_records(book_id: BookId, lang: str = "ru", *, store: Path | None = None) -> list[LineRecord]:
    """A book's records via the on-disk cache, validated against the live docx. FAILS LOUD on a
    missing/stale artifact — never re-emits (build the cache with `build_records`)."""
    return artifact.load_records_artifact(
        paths.book_docx(book_id, lang), lang, book_id, store=store or paths.ARTIFACT_STORE)


def load_records_many(
    book_ids: Iterable[BookId], lang: str = "ru", *, store: Path | None = None,
) -> RecordsByBook:
    """Records for several books, keyed by book_id — loaded once at the shell so domain code can
    take the whole `{book: records}` map as data instead of reaching for each book itself."""
    return {b: load_records(b, lang, store=store) for b in book_ids}


# --- teacher loop IO: task bundles (manifest committed, payload derived) + promotion -----------

TASKS_DIR = "tasks"            # committed: <task_id>.manifest.json — resolves L001→LineId
RESPONSES_DIR = "responses"    # committed: <task_id>.json — raw human adjudications
PANEL_RUNS_DIR = "panel_runs"  # committed: <run_id>.jsonl — per-rep panel votes (evidence)


def save_task_bundle(task_id: str, payload: dict[str, Any], manifest: dict[str, Any], *,
                     annotations: Path | None = None, store: Path | None = None) -> None:
    """Persist a task: the MANIFEST (committed source — the only thing that resolves the opaque
    keys) under `annotations/tasks/`, and the reader/UI PAYLOAD (derived — regenerable from
    records + recipe) under `_teacher/`."""
    ann = annotations or paths.ANNOTATIONS
    st = store or paths.TEACHER_STORE
    (ann / TASKS_DIR).mkdir(parents=True, exist_ok=True)
    (ann / TASKS_DIR / f"{task_id}.manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2))
    (st / task_id).mkdir(parents=True, exist_ok=True)
    (st / task_id / "payload.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2))


def load_task_bundle(task_id: str, *, annotations: Path | None = None,
                     store: Path | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """The `(payload, manifest)` for a task — payload from the derived store, manifest from the
    committed source. FAILS LOUD if either is missing."""
    ann = annotations or paths.ANNOTATIONS
    st = store or paths.TEACHER_STORE
    payload = json.loads(_must(st / task_id / "payload.json").read_text())
    manifest = json.loads(_must(ann / TASKS_DIR / f"{task_id}.manifest.json").read_text())
    return payload, manifest


def save_human_responses(task_id: str, data: dict[str, Any], *,
                         annotations: Path | None = None) -> None:
    """A human adjudication export (`adjudicate.html` output) — irreplaceable source, committed."""
    ann = annotations or paths.ANNOTATIONS
    (ann / RESPONSES_DIR).mkdir(parents=True, exist_ok=True)
    (ann / RESPONSES_DIR / f"{task_id}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2))


def load_human_responses(task_id: str, *, annotations: Path | None = None) -> dict[str, Any]:
    ann = annotations or paths.ANNOTATIONS
    return json.loads(_must(ann / RESPONSES_DIR / f"{task_id}.json").read_text())


def save_panel_reps(run_id: str, rows: list[dict[str, Any]], *,
                    annotations: Path | None = None) -> None:
    """Per-rep parsed panel votes — committed EVIDENCE (run-to-run instability is real data, not
    fluff). The canonical resolved view is `votes.jsonl`; this keeps the reps behind it."""
    ann = annotations or paths.ANNOTATIONS
    (ann / PANEL_RUNS_DIR).mkdir(parents=True, exist_ok=True)
    artifact.write_jsonl(ann / PANEL_RUNS_DIR / f"{run_id}.jsonl", rows)


def load_panel_reps(run_id: str, *, annotations: Path | None = None) -> list[dict[str, Any]]:
    ann = annotations or paths.ANNOTATIONS
    return list(artifact.read_jsonl(_must(ann / PANEL_RUNS_DIR / f"{run_id}.jsonl")))


# --- resumable raw calls: one persisted (item, reader, rep) reply per LLM call (derived) ------

CALLS_FILE = "calls.jsonl"     # derived: append-once raw replies, the run's resume log


def save_panel_call(task_id: str, row: dict[str, Any], *, store: Path | None = None) -> None:
    """APPEND one completed call's raw reply (`{item_id, tag, rep, model, content, finish_reason}`)
    to the run's resume log under `_teacher/`. Persisted the instant the call returns, BEFORE
    parsing, so a malformed reply survives and a re-run reuses it instead of re-paying for it. The
    log is derived (regenerable by re-calling), so it lives beside the payload, not in committed
    truth."""
    st = store or paths.TEACHER_STORE
    path = st / task_id / CALLS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_panel_calls(task_id: str, *, store: Path | None = None) -> list[dict[str, Any]]:
    """Every saved raw call for a task (empty if none yet) — the resume cache. Last write wins per
    `(item_id, tag, rep)` is the caller's job; this returns rows in append order."""
    st = store or paths.TEACHER_STORE
    path = st / task_id / CALLS_FILE
    if not path.is_file():
        return []
    return list(artifact.read_jsonl(path))


# --- promotion: resolved rows → the committed truth the eval half loads (atomic) ---------------

def _atomic_text(path: Path, text: str) -> None:
    """Write via a temp file + atomic replace, so a committed-truth file is never left half-written
    if a promote crashes mid-write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def _jsonl(rows: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)


def write_label_rows(rows: list[dict[str, Any]], *, annotations: Path | None = None) -> None:
    _atomic_text((annotations or paths.ANNOTATIONS) / LABELS_FILE, _jsonl(rows))


def write_vote_rows(rows: list[dict[str, Any]], *, annotations: Path | None = None) -> None:
    _atomic_text((annotations or paths.ANNOTATIONS) / VOTES_FILE, _jsonl(rows))


def write_eval_set(name: str, rows: list[dict[str, Any]], *,
                   annotations: Path | None = None) -> None:
    _atomic_text((annotations or paths.ANNOTATIONS) / "eval_sets" / f"{name}.json",
                 json.dumps(rows, ensure_ascii=False, indent=2))
