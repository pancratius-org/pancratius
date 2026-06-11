# research-pure: THE I/O boundary — the only module that knows where annotations + records live.
"""The single read/write edge. Domain code stays pure (it takes data as arguments); every disk
path lives here. The committed annotation TRUTH (`annotations/`) and the derived record CACHE
(`_artifacts/`) are both reached through this module, so a layout change touches one file and a
domain function can never read a path behind the caller's back.

Annotation files are returned RAW (label/vote rows as dicts; eval-set memberships and selections
as `LineId`-key lists); interpreting them into typed records (reject unmapped, group by reader,
join slice truth, …) is pure and lives in `annotations.py` / `evaluation.datasets`. Record
artifacts are returned as validated `LineRecord`s through the existing hash-railed `artifact`
loader."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tomllib
from collections.abc import Iterable
from pathlib import Path

from . import artifact, paths
from .identity import BookId, JsonObject, JsonRow, LineKey, RunId, TaskId
from .records import LineRecord, RecordsByBook

LABELS_FILE = "labels.jsonl"   # the resolved per-line truth
VOTES_FILE = "votes.jsonl"     # the LLM panel votes


# --- committed annotation TRUTH (source; never rebuilt) ---------------------------------------

def _rows(path: Path) -> list[JsonRow]:
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


def load_label_rows(*, annotations: Path | None = None) -> list[JsonRow]:
    return _rows((annotations or paths.ANNOTATIONS) / LABELS_FILE)


def load_vote_rows(*, annotations: Path | None = None) -> list[JsonRow]:
    return _rows((annotations or paths.ANNOTATIONS) / VOTES_FILE)


def load_eval_set(name: str, *, annotations: Path | None = None) -> list[LineKey]:
    """The committed MEMBERSHIP of a named eval slice (`eval_sets/<name>.json`) — a frozen
    `LineId`-key list naming WHICH lines an eval scores. Membership only: the truth for these
    lines is always read from `labels.jsonl` (see `evaluation.datasets.eval_slice`), so the same
    line can never be scored against two stores. FAILS LOUD if missing."""
    path = (annotations or paths.ANNOTATIONS) / "eval_sets" / f"{name}.json"
    if not path.is_file():
        raise FileNotFoundError(f"committed eval set missing: {path}")
    return json.loads(path.read_text())


def load_selection(name: str, *, annotations: Path | None = None) -> list[LineKey]:
    """A committed `LineId`-key list (`selections/<name>.json`) — e.g. the active-learning acquire
    set, written as DATA by the eval/student side so the teacher consumes it without importing it.
    Each entry is a `LineId.as_key()` 4-list, resolved via `LineId.from_key` by the reader.
    FAILS LOUD if missing."""
    path = (annotations or paths.ANNOTATIONS) / "selections" / f"{name}.json"
    if not path.is_file():
        raise FileNotFoundError(f"committed selection missing: {path}")
    return json.loads(path.read_text())


# --- study provenance: git SHA + file fingerprints (read-only) ---------------------------------

def git_sha(*, repo: Path | None = None) -> str:
    """The repo's `HEAD` commit SHA, with `+dirty` appended if the working tree has uncommitted
    changes — the provenance stamp a study writes into its manifest so a scorecard is traceable to the
    exact code that produced it. Read-only; the shell passes the result in, never the manifest builder."""
    root = repo or paths.REPO_ROOT
    sha = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                         capture_output=True, text=True, check=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                           capture_output=True, text=True, check=True).stdout.strip()
    return f"{sha}+dirty" if dirty else sha


def sha256_file(path: Path) -> str:
    """The sha256 hex of a file's bytes — the fingerprint a manifest pins for the eval set / prompts,
    so a replayed study fails loud if its inputs drifted. FAILS LOUD if the file is missing."""
    return hashlib.sha256(_must(path).read_bytes()).hexdigest()


def load_prices(*, path: Path | None = None) -> JsonObject:
    """The committed OpenRouter price table (`evaluation/prices.toml`) as the RAW parsed dict — a
    `version` stamp + per-model `{prompt, completion}` $/token. The one disk read for pricing; the
    caller (`evaluation.prices.PriceTable.from_dict`) parses it into the typed table, so this disk
    boundary never imports up into `evaluation/`. FAILS LOUD if missing — pricing is committed data,
    not derived."""
    p = path or (Path(__file__).resolve().parent / "evaluation" / "prices.toml")
    if not p.is_file():
        raise FileNotFoundError(f"committed price table missing: {p}")
    return tomllib.loads(p.read_text())


def load_prompt(filename: str, *, prompts_dir: Path | None = None) -> str:
    """A committed model-facing reader prompt (`campaigns/prompts/<filename>`) a recipe references by
    name. The store is the one disk boundary, so the recipe loader reads prompts through here, not
    directly. FAILS LOUD if missing — a recipe naming a prompt that isn't there is a config error,
    never a silent empty prompt."""
    path = (prompts_dir or paths.PROMPTS) / filename
    if not path.is_file():
        raise FileNotFoundError(f"recipe prompt file missing: {path}")
    return path.read_text()


# --- study experiment folders: durable evidence + a derived reply resume cache ----------------
# A study writes ONLY here (never `annotations/`): the three durable files atomically, and an
# append-once `replies.jsonl` resume cache so a re-run re-pays for NOTHING already fetched.

SCORECARD_FILE = "scorecard.json"
REPORT_FILE = "report.md"
EXPERIMENT_MANIFEST_FILE = "manifest.json"
REPLIES_FILE = "replies.jsonl"


def write_experiment(experiment_id: str, *, scorecard: JsonObject, report: str,
                     manifest: JsonObject, experiments: Path | None = None) -> Path:
    """Persist a study's durable result into its folder (created if absent): `scorecard.json` +
    `report.md` + `manifest.json`, each written atomically. Returns the folder. The ONLY writer of an
    experiment folder; it never touches `annotations/` — a study produces evidence, not truth."""
    folder = (experiments or paths.EXPERIMENTS) / experiment_id
    folder.mkdir(parents=True, exist_ok=True)
    _atomic_text(folder / SCORECARD_FILE, json.dumps(scorecard, ensure_ascii=False, indent=2))
    _atomic_text(folder / REPORT_FILE, report)
    _atomic_text(folder / EXPERIMENT_MANIFEST_FILE, json.dumps(manifest, ensure_ascii=False, indent=2))
    return folder


def load_experiment_timestamp(experiment_id: str, *,
                              experiments: Path | None = None) -> str | None:
    """A prior run's manifest timestamp, if one exists — so a replay preserves WHEN THE EVIDENCE WAS
    FIRST PRODUCED (a $0 re-run over a complete cache rewrites byte-identical files, never re-stamping
    a replay as if it were fresh)."""
    path = (experiments or paths.EXPERIMENTS) / experiment_id / EXPERIMENT_MANIFEST_FILE
    if not path.is_file():
        return None
    ts = json.loads(path.read_text()).get("timestamp")
    return str(ts) if ts else None


def save_experiment_reply(experiment_id: str, row: JsonRow, *,
                          experiments: Path | None = None) -> None:
    """APPEND one fresh panel reply to the experiment folder's `replies.jsonl` resume cache, the
    instant it lands (before parse), so a crashed/interrupted study re-pays for nothing. Derived
    (regenerable by re-calling), so gitignored unless a claim needs it to reproduce."""
    folder = (experiments or paths.EXPERIMENTS) / experiment_id
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / REPLIES_FILE).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_experiment_replies(experiment_id: str, *,
                            experiments: Path | None = None) -> list[JsonRow]:
    """Every saved reply for a study (empty if none yet) — the resume cache, in append order."""
    path = (experiments or paths.EXPERIMENTS) / experiment_id / REPLIES_FILE
    if not path.is_file():
        return []
    return list(artifact.read_jsonl(path))


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


def save_task_bundle(task_id: TaskId, payload: JsonObject, manifest: JsonObject, *,
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


def load_task_bundle(task_id: TaskId, *, annotations: Path | None = None,
                     store: Path | None = None) -> tuple[JsonObject, JsonObject]:
    """The `(payload, manifest)` for a task — payload from the derived store, manifest from the
    committed source. FAILS LOUD if either is missing."""
    ann = annotations or paths.ANNOTATIONS
    st = store or paths.TEACHER_STORE
    payload = json.loads(_must(st / task_id / "payload.json").read_text())
    manifest = json.loads(_must(ann / TASKS_DIR / f"{task_id}.manifest.json").read_text())
    return payload, manifest


def task_manifest_exists(task_id: TaskId, *, annotations: Path | None = None) -> bool:
    """Whether a task's committed manifest is already on disk — the predicate `route` consults before
    it would re-mint an adjudication sub-task's keys (re-minting over live human responses corrupts
    them)."""
    return ((annotations or paths.ANNOTATIONS) / TASKS_DIR / f"{task_id}.manifest.json").is_file()


def save_human_responses(task_id: TaskId, data: JsonObject, *,
                         annotations: Path | None = None) -> None:
    """A human adjudication export (`adjudicate.html` output) — irreplaceable source, committed."""
    ann = annotations or paths.ANNOTATIONS
    (ann / RESPONSES_DIR).mkdir(parents=True, exist_ok=True)
    (ann / RESPONSES_DIR / f"{task_id}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2))


def load_human_responses(task_id: TaskId, *, annotations: Path | None = None) -> JsonObject:
    ann = annotations or paths.ANNOTATIONS
    return json.loads(_must(ann / RESPONSES_DIR / f"{task_id}.json").read_text())


def save_panel_reps(run_id: RunId, rows: list[JsonRow], *,
                    annotations: Path | None = None) -> None:
    """Per-rep parsed panel votes — committed EVIDENCE (run-to-run instability is real data, not
    fluff). The canonical resolved view is `votes.jsonl`; this keeps the reps behind it."""
    ann = annotations or paths.ANNOTATIONS
    (ann / PANEL_RUNS_DIR).mkdir(parents=True, exist_ok=True)
    artifact.write_jsonl(ann / PANEL_RUNS_DIR / f"{run_id}.jsonl", rows)


def load_panel_reps(run_id: RunId, *, annotations: Path | None = None) -> list[JsonRow]:
    ann = annotations or paths.ANNOTATIONS
    return list(artifact.read_jsonl(_must(ann / PANEL_RUNS_DIR / f"{run_id}.jsonl")))


# --- resumable raw calls: one persisted (item, reader, rep) reply per LLM call (derived) ------

CALLS_FILE = "calls.jsonl"     # derived: append-once raw replies, the run's resume log


def save_panel_call(task_id: TaskId, row: JsonRow, *, store: Path | None = None) -> None:
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


def load_panel_calls(task_id: TaskId, *, store: Path | None = None) -> list[JsonRow]:
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


def _jsonl(rows: list[JsonRow]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)


def write_label_rows(rows: list[JsonRow], *, annotations: Path | None = None) -> None:
    _atomic_text((annotations or paths.ANNOTATIONS) / LABELS_FILE, _jsonl(rows))


def write_vote_rows(rows: list[JsonRow], *, annotations: Path | None = None) -> None:
    _atomic_text((annotations or paths.ANNOTATIONS) / VOTES_FILE, _jsonl(rows))


def write_eval_set(name: str, keys: list[LineKey], *,
                   annotations: Path | None = None) -> None:
    """Commit an eval slice's MEMBERSHIP (`LineId` keys only — its truth lives in `labels.jsonl`)."""
    _atomic_text((annotations or paths.ANNOTATIONS) / "eval_sets" / f"{name}.json",
                 json.dumps(keys, ensure_ascii=False, indent=2))


def save_selection(name: str, keys: list[LineKey], *, annotations: Path | None = None) -> None:
    """Commit a `LineId`-key list (each key is `LineId.as_key()` = `[lang, book_id, src_ordinal,
    sub]`) as `selections/<name>.json` — e.g. the active-learning acquire set the teacher reads via
    `selector="selection_file:<name>"`. Written by the eval/student side as DATA, so the teacher
    consumes it without importing the student. The read side is `load_selection`."""
    _atomic_text((annotations or paths.ANNOTATIONS) / "selections" / f"{name}.json",
                 json.dumps(keys, ensure_ascii=False, indent=2))
