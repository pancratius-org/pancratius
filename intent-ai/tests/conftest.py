# research-pure: test bootstrap — put the package src on the path; require truth + record cache.
"""Makes `import intent_ai` resolve and asserts both halves of the store are present:

  - the committed annotation TRUTH (`annotations/`) — source data, never rebuilt;
  - the derived record CACHE (`_artifacts/`) — rebuilt from the committed DOCX by `build_records`.

The package is LOAD-ONLY: every consumer reads these and fails loud if missing — it never
rebuilds on the fly. A missing half is a setup error surfaced here once, not as N opaque
per-test failures."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _require_store() -> None:
    from intent_ai import artifact, paths, store

    if not (paths.ANNOTATIONS / store.LABELS_FILE).is_file():
        raise RuntimeError(
            f"committed annotation truth missing at {paths.ANNOTATIONS} — it is source data, not "
            f"rebuilt; restore it before running the suite.")
    if not any(paths.ARTIFACT_STORE.glob(f"*/{artifact.RECORDS_FILE}")):
        raise RuntimeError(
            f"record cache missing at {paths.ARTIFACT_STORE} — run "
            f"`uv run --project intent-ai --frozen python -m intent_ai.build_records` to rebuild "
            "it from "
            f"the committed DOCX.")


_require_store()


@pytest.fixture(scope="session")
def corpus():
    """Committed labels + the records for their books, loaded once at the test edge — domain
    functions take this data as arguments; they never read it themselves."""
    from intent_ai import store
    from intent_ai.annotations import load_labels
    labelset = load_labels()
    records = store.load_records_many(sorted({g.id.book_key for g in labelset.labels}))
    return records, labelset


@pytest.fixture(scope="session")
def student_predictions(corpus):
    """One book-held-out prediction map shared by every downstream judge."""
    from intent_ai import student

    records, labelset = corpus
    dataset = student.build_dataset(records, labelset)
    return {
        line_id: decision.label
        for line_id, decision in student.oof_smoothed(dataset, records).items()
    }
