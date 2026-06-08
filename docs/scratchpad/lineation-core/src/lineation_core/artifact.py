# research-pure: the on-disk artifact — built once, read many, fail loud on drift.
"""The canonical on-disk record artifact (the SPEC product). Records, schema, and a manifest are
written to disk ONCE per (book, lang) by the explicit BUILD path; every consumer is a VIEW over
the read-back artifact, never a live `read_lines`. The annotation TRUTH (labels/votes/eval sets)
is store-level committed data in `annotations/`, not a per-book artifact — `store` owns it.

Files in an artifact directory:
    line_records.jsonl       all LineRecords for (book, lang)
    feature_schema.json      feature schema + version + feature_support (zero-support listed)
    manifest.json            producer/schema versions, docx hash, lang, counts

Build vs load are SEPARATE: `build_records_artifact` is the one place the producer runs and
emits; `load_records_artifact` only reads back through the rails and NEVER re-emits. A
consumer or test that loads a missing/stale artifact FAILS LOUD — it does not silently
rebuild (which would trigger a render). Safety rails (never silent): the manifest pins the
producer/schema versions and the docx package hash. A producer/schema skew is ALWAYS fatal
(the feature space on disk is different); a docx-hash mismatch is fatal unless the caller
opts into an explicit migration. Per-record text hashes are re-validated for consistency.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from .identity import BookId, DocxPackageHash, JsonObject, JsonRow, LineId, text_hash
from .records import FeatureSchema, LineRecord, feature_field_names

# Bumped when the feature set changes shape (a feature added/removed/renamed) or the
# producer's structural semantics change. A loaded artifact whose version differs fails loud.
FEATURE_SCHEMA_VERSION = "features-2"
PRODUCER_VERSION = "read_lines-2"

RECORDS_FILE = "line_records.jsonl"
SCHEMA_FILE = "feature_schema.json"
MANIFEST_FILE = "manifest.json"


class HashMismatch(RuntimeError):
    """A stored artifact's validation hash does not match the live docx/schema. The loader
    raises this instead of silently trusting stale data."""


@dataclass(frozen=True)
class Manifest:
    producer_version: str
    feature_schema_version: str
    docx_package_hash: DocxPackageHash
    lang: str
    book_id: BookId
    n_records: int

    def to_dict(self) -> JsonObject:
        return {
            "producer_version": self.producer_version,
            "feature_schema_version": self.feature_schema_version,
            "docx_package_hash": self.docx_package_hash,
            "lang": self.lang, "book_id": self.book_id, "n_records": self.n_records,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, object]) -> Self:
        return cls(
            producer_version=d["producer_version"],
            feature_schema_version=d["feature_schema_version"],
            docx_package_hash=d["docx_package_hash"], lang=d["lang"],
            book_id=d["book_id"], n_records=d["n_records"],
        )

    def check(self, *, live_docx_hash: DocxPackageHash, migration: bool = False) -> None:
        """Fail loud unless the artifact matches the running producer/schema/docx. With
        `migration=True` the docx-hash rail is relaxed (an explicit, logged remap), but a
        producer/schema version skew is ALWAYS fatal — those mean the features on disk are a
        different feature space."""
        if self.producer_version != PRODUCER_VERSION:
            raise HashMismatch(
                f"producer_version {self.producer_version!r} != live {PRODUCER_VERSION!r}")
        if self.feature_schema_version != FEATURE_SCHEMA_VERSION:
            raise HashMismatch(
                f"feature_schema_version {self.feature_schema_version!r} != live "
                f"{FEATURE_SCHEMA_VERSION!r}")
        if self.docx_package_hash != live_docx_hash and not migration:
            raise HashMismatch(
                f"docx_package_hash {self.docx_package_hash!r} != live {live_docx_hash!r} "
                f"for book {self.book_id} {self.lang}; pass migration=True to override")


def write_jsonl(path: Path, rows: Iterable[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> Iterator[JsonRow]:
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def build_schema(records: Iterable[LineRecord]) -> FeatureSchema:
    """The feature schema for a record set, with feature_support. A schema field whose
    vector column is non-zero on no row is reported as zero-support (and stays VISIBLE) —
    derived from the producer's column space so a zero-support categorical level is not
    invented or lost."""
    from . import producer  # local: producer imports records, not artifact

    fields = feature_field_names()
    support = {c: 0 for c in producer.vector_columns()}
    for rec in records:
        for col, v in producer.vectorize_fixed(rec.features).items():
            if v != 0.0:
                support[col] += 1
    return FeatureSchema(FEATURE_SCHEMA_VERSION, PRODUCER_VERSION, fields, support)


def emit(
    out_dir: Path, records: list[LineRecord], *, lang: str, book_id: BookId,
    docx_hash: DocxPackageHash,
) -> Manifest:
    """Write the record artifact for one (book, lang): records + feature schema + manifest.
    Returns the manifest written. The annotation TRUTH is committed separately in `annotations/`
    (it has no rebuilder); this path only emits the rebuildable record cache."""
    out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(out_dir / RECORDS_FILE, (r.to_dict() for r in records))
    (out_dir / SCHEMA_FILE).write_text(
        json.dumps(build_schema(records).to_dict(), ensure_ascii=False, indent=2))
    manifest = Manifest(PRODUCER_VERSION, FEATURE_SCHEMA_VERSION, docx_hash, lang, book_id,
                        len(records))
    (out_dir / MANIFEST_FILE).write_text(json.dumps(manifest.to_dict(), indent=2))
    return manifest


def load_records(
    records_path: Path, manifest_path: Path, *, live_docx_hash: DocxPackageHash,
    migration: bool = False,
) -> list[LineRecord]:
    """Load records, FAILING LOUD if the manifest's hashes do not match the live docx or
    the running producer/schema. Per-record hashes are NOT silently trusted: every record
    is re-validated against its own `line_text_hash` for internal consistency, and a
    duplicate `LineId` or a count mismatch is fatal."""
    manifest = Manifest.from_dict(json.loads(manifest_path.read_text()))
    manifest.check(live_docx_hash=live_docx_hash, migration=migration)
    out: list[LineRecord] = []
    seen: set[LineId] = set()
    for d in read_jsonl(records_path):
        rec = LineRecord.from_dict(d)
        if rec.id in seen:
            raise HashMismatch(f"duplicate LineId in artifact: {rec.id}")
        seen.add(rec.id)
        if text_hash(rec.text) != rec.line_text_hash:
            raise HashMismatch(
                f"line_text_hash mismatch for {rec.id}: stored text does not hash to "
                f"{rec.line_text_hash}")
        out.append(rec)
    if len(out) != manifest.n_records:
        raise HashMismatch(f"manifest n_records={manifest.n_records} but loaded {len(out)}")
    return out


def load_artifact(
    out_dir: Path, *, live_docx_hash: DocxPackageHash, migration: bool = False
) -> list[LineRecord]:
    """Load a whole artifact directory's records by its standard file names."""
    return load_records(out_dir / RECORDS_FILE, out_dir / MANIFEST_FILE,
                        live_docx_hash=live_docx_hash, migration=migration)


def artifact_dir(store: Path, book_id: BookId, lang: str) -> Path:
    return store / f"{book_id}-{lang}"


def build_records_artifact(
    docx: Path, lang: str, book_id: BookId, *, store: Path,
) -> list[LineRecord]:
    """THE build path — the only place the producer runs and a record artifact is emitted.
    Renders the records for (book, lang), writes them into `store/<book>-<lang>/`, then loads
    them back through the rails so the build returns the SAME validated records a consumer would.
    Consumers never call this; only `build_records` does."""
    from . import identity, producer

    out_dir = artifact_dir(store, book_id, lang)
    docx_hash = identity.docx_package_hash(docx)
    emit(out_dir, producer.read_lines(docx, lang, book_id), lang=lang, book_id=book_id,
         docx_hash=docx_hash)
    return load_artifact(out_dir, live_docx_hash=docx_hash)


def load_records_artifact(docx: Path, lang: str, book_id: BookId, *, store: Path) -> list[LineRecord]:
    """THE consumer read path: load the on-disk records for (book, lang), validated against
    the live docx through the fail-loud rails. NEVER emits — a missing or stale artifact
    raises (HashMismatch / FileNotFoundError) rather than triggering a render. Build the store
    first with `build.py`; consumers and tests load-only."""
    from . import identity

    out_dir = artifact_dir(store, book_id, lang)
    return load_artifact(out_dir, live_docx_hash=identity.docx_package_hash(docx))
