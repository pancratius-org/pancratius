# research-pure: test bootstrap — put the package src on the path.
"""Portable tests need no local compiler or derived corpus cache.

The corpus-acceptance fixture remains load-only and fails loud when its explicit
local preparation is absent. Importing an unrelated unit test never materializes
or requires the 302 MB cache.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """A cache-backed fixture is an explicit acceptance-test capability."""
    for item in items:
        if {"corpus", "student_predictions"}.isdisjoint(item.fixturenames):
            continue
        if item.get_closest_marker("corpus_cache") is None:
            raise pytest.UsageError(
                f"{item.nodeid} reaches the ignored corpus cache without "
                "@pytest.mark.corpus_cache"
            )


def _require_corpus_store() -> None:
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


@pytest.fixture
def synthetic_record_store(monkeypatch: pytest.MonkeyPatch):
    """Replace the artifact repository at its sanctioned edge with typed records.

    The returned setter installs deliberately shaped editions. An unconfigured
    edition fails, so a wrong-book read cannot look plausibly successful.
    """
    from intent_ai import store
    from intent_ai.identity import BookId, BookKey
    from intent_ai.records import LineRecord

    configured: dict[BookKey, list[LineRecord]] = {}

    def put(records: list[LineRecord]) -> None:
        if not records:
            raise ValueError("a synthetic record edition cannot be empty")
        editions = {record.id.book_key for record in records}
        if len(editions) != 1:
            raise ValueError("synthetic records must describe exactly one edition")
        configured[editions.pop()] = records

    def load_records(
        book_id: BookId | str,
        lang: str = "ru",
        *,
        store=None,
    ) -> list[LineRecord]:
        del store
        key = BookKey(lang, BookId(book_id))
        try:
            return list(configured[key])
        except KeyError as exc:
            raise AssertionError(f"test read unconfigured record edition {key}") from exc

    monkeypatch.setattr(store, "load_records", load_records)
    return put


@pytest.fixture
def sample_record_store(synthetic_record_store):
    """A closed in-memory repository for the two editions used by shell tests."""
    from intent_ai.identity import BookId

    from tests.record_factory import sample_records

    for book_id in (BookId("13"), BookId("57")):
        synthetic_record_store(sample_records(book_id=book_id))
    return synthetic_record_store


@pytest.fixture(scope="session")
def corpus():
    """Committed labels + the records for their books, loaded once at the test edge — domain
    functions take this data as arguments; they never read it themselves."""
    _require_corpus_store()
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
