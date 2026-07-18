# intent-ai

Research package for recovering authorial lineation from the library's DOCX sources.

The production library owns DOCX syntax and semantics. `intent-ai` consumes its typed
`DocxSourceDocument`; it does not parse OOXML, reconstruct Pandoc blocks, or maintain a second text
surface. Each source paragraph is projected into canonical natural lines, measured, and represented
as one `LineRecord` per `(language, book, paragraph ordinal, natural-line index)`.

Those records support three downstream operations:

- train and evaluate an interpretable `prose | lineated` student;
- acquire and adjudicate new labels with richer teacher evidence;
- export human corrections as static importer sidecars.

Production never imports this package. Committed state is annotation truth, raw panel/adjudication
evidence, experiment reports, and migration lineage. Historical results are versioned under
`experiments/history/`; `_artifacts/` is a derived, ignored cache rebuilt from source DOCX.

```sh
uv run --project intent-ai --frozen python -m intent_ai.build_records
uv run --project intent-ai --group dev --frozen pytest intent-ai/tests -q -c intent-ai/pyproject.toml
```

See `SPEC.md` for the data contract and `ARCHITECTURE.md` for boundaries and flow.
