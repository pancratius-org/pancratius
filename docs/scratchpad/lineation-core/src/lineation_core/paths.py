# research-pure: corpus path anchors — located by a repo marker, never by parents[N] counting.
"""One source of truth for the corpus locations this package reads (read-only).

The repo root is found by walking up to the directory that holds the `pancratius` package
and `pyproject.toml` — so no module counts `.parents[N]` (a brittle, miscountable index that
silently breaks when a file moves). Everything else is named off that anchor.

The package reads only the source DOCX (to validate records against) and its own artifact
store. The raw annotation sources (the prior study's per-line shards, panel reader files, and
human re-adjudications) are NOT package inputs — the canonical artifacts were derived from them
once and are committed; this package reads only the committed store and the source DOCX.
"""
from __future__ import annotations

from pathlib import Path

from .identity import BookId


def _find_repo_root(start: Path) -> Path:
    for d in (start, *start.parents):
        if (d / "pyproject.toml").is_file() and (d / "pancratius").is_dir():
            return d
    raise RuntimeError(f"repo root (pyproject.toml + pancratius/) not found above {start}")


REPO_ROOT = _find_repo_root(Path(__file__).resolve())

BOOKS = REPO_ROOT / "src" / "content" / "books"

# the rebuildable record cache: line_records.jsonl + feature_schema + manifest per book/lang,
# emitted from the committed DOCX by `build_records` and validated fail-loud on load. Derived &
# gitignored — safe to delete and rebuild; NOT source of truth.
ARTIFACT_STORE = REPO_ROOT / "docs" / "scratchpad" / "lineation-core" / "_artifacts"

# the committed annotation TRUTH: labels.jsonl (the ONE label store) + votes.jsonl (LineId-keyed),
# eval-slice memberships under eval_sets/*.json (keys only — their truth is labels.jsonl), and the
# teacher loop's irreplaceable source — task manifests (resolve the
# opaque keys), raw human responses, per-rep panel votes. Committed, never rebuilt.
ANNOTATIONS = REPO_ROOT / "docs" / "scratchpad" / "lineation-core" / "annotations"

# derived teacher work: reader/UI task payloads + raw model replies, regenerable from records +
# recipe. Gitignored like the record cache; the irreplaceable manifests/responses are committed.
TEACHER_STORE = REPO_ROOT / "docs" / "scratchpad" / "lineation-core" / "_teacher"

# authored campaign inputs: run recipes (TOML) + the model-facing reader prompts they reference by
# filename. Committed source — a recipe names a prompt; the loader reads it from PROMPTS.
CAMPAIGNS = REPO_ROOT / "docs" / "scratchpad" / "lineation-core" / "campaigns"
PROMPTS = CAMPAIGNS / "prompts"

# the lab-notebook units: one folder per experiment, holding experiment.toml + the durable scorecard/
# report/manifest + a derived replies.jsonl resume cache. A study writes ONLY here, never into
# ANNOTATIONS — it produces EVIDENCE, never truth.
EXPERIMENTS = REPO_ROOT / "docs" / "scratchpad" / "lineation-core" / \
    "src" / "lineation_core" / "evaluation" / "experiments"


def book_docx(book_id: BookId, lang: str = "ru") -> Path:
    """The source DOCX for a book folder number (`"37"`) — the read-only substrate a record
    artifact is validated against on load."""
    matches = sorted(BOOKS.glob(f"{book_id}-*"))
    if not matches:
        raise FileNotFoundError(f"no book folder for {book_id}")
    docx = matches[0] / f"{lang}.docx"
    if not docx.is_file():
        raise FileNotFoundError(f"no {lang}.docx in {matches[0].name}")
    return docx


def corpus_books(lang: str) -> list[BookId]:
    """Every book folder number with a committed `<lang>.docx` — the whole corpus for that
    language, enumerated from disk so no module hard-codes a book list."""
    return sorted(d.name.split("-", 1)[0] for d in BOOKS.iterdir()
                  if d.is_dir() and (d / f"{lang}.docx").is_file())
