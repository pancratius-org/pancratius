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
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from . import artifact, paths
from .records import LineRecord

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


# --- derived record CACHE (load-only, hash-railed; built by `build_records`) -------------------

def load_records(book_id: str, lang: str = "ru", *, store: Path | None = None) -> list[LineRecord]:
    """A book's records via the on-disk cache, validated against the live docx. FAILS LOUD on a
    missing/stale artifact — never re-emits (build the cache with `build_records`)."""
    return artifact.load_records_artifact(
        paths.book_docx(book_id, lang), lang, book_id, store=store or paths.ARTIFACT_STORE)


def load_records_many(
    book_ids: Iterable[str], lang: str = "ru", *, store: Path | None = None,
) -> dict[str, list[LineRecord]]:
    """Records for several books, keyed by book_id — loaded once at the shell so domain code can
    take the whole `{book: records}` map as data instead of reaching for each book itself."""
    return {b: load_records(b, lang, store=store) for b in book_ids}
