#!/usr/bin/env python3
"""Capture and compare the DOCX frontend at its stable compiler seams.

The snapshots live outside the repository.  Capture the old frontend before the
reader switch, capture the replacement afterwards, then compare the directories:

    uv run tests/tools/docx_frontend_parity.py capture \
      --all --output /tmp/pancratius-docx-old
    uv run tests/tools/docx_frontend_parity.py compare \
      /tmp/pancratius-docx-old /tmp/pancratius-docx-new

Each compressed snapshot contains source facts, adapted block IR, post-Q1 IR,
post-Q2 IR, lowered Markdown, assets, and diagnostics.  Runtime measurements are
recorded but excluded from semantic comparison.
"""

from __future__ import annotations

import argparse
import dataclasses
import gzip
import hashlib
import json
import re
import sys
import tempfile
import time
import tracemalloc
from collections.abc import Iterable, Iterator, Mapping, Sequence
from enum import Enum
from pathlib import Path
from typing import Any, cast

from pancratius import (
    cross_refs,
    docx_adapter,
    docx_conversion,
    docx_source,
    ir,
    lineation_overrides,
    lower,
    rights_boilerplate,
    scripture_overrides,
)
from pancratius.content_catalog import (
    CatalogEntry,
    build_title_index,
    scan_catalog,
)
from pancratius.intent_inference import artifacts as register_artifacts
from pancratius.ir.inlines import inline_plain
from pancratius.passes import assets
from pancratius.passes.pipeline import (
    BOOK_PASSES,
    POEM_PASSES,
    POST_FOLD_SEAM,
    BibliographyLookup,
    Context,
    LineationCorrections,
    ScripturePins,
    run,
)

ROOT = Path(__file__).resolve().parents[2]
CONTENT_ROOT = ROOT / "src" / "content"
SCHEMA = 2
CANONICAL_SOURCE_DIR = "_canonical_source"
type Snapshot = dict[str, Any]


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _sanitize_string(value: str, media_dir: Path | None) -> str:
    if media_dir is not None:
        value = value.replace(str(media_dir), "$MEDIA")
    return value.replace(str(ROOT), "$ROOT")


def _json_value(value: object, *, media_dir: Path | None = None) -> object:
    """Lossless stable JSON projection of the typed IR.

    The legacy frontend's raw table payload is deliberately omitted. Structured
    cells and the resulting bibliography/output are the compiler contracts that
    a replacement must preserve.
    """
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, str):
        return _sanitize_string(value, media_dir)
    if isinstance(value, Path):
        return _sanitize_string(value.as_posix(), media_dir)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        fields: dict[str, object] = {"$type": type(value).__name__}
        for field in dataclasses.fields(value):
            if isinstance(value, ir.Table) and field.name == "raw":
                continue
            fields[field.name] = _json_value(
                getattr(value, field.name), media_dir=media_dir
            )
        return fields
    if isinstance(value, Mapping):
        return {
            str(key): _json_value(item, media_dir=media_dir)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, set | frozenset):
        items = [_json_value(item, media_dir=media_dir) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True))
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        return [_json_value(item, media_dir=media_dir) for item in value]
    raise TypeError(f"cannot snapshot {type(value).__name__}")


def _atom(atom: docx_source.ParagraphAtom) -> list[str]:
    if isinstance(atom, docx_source.TextAtom):
        return ["text", atom.value]
    return ["break", atom.value]


def _source_snapshot(source: docx_source.DocxSourceDocument) -> dict[str, object]:
    paragraphs: list[dict[str, object]] = []
    for paragraph in source.paragraphs:
        paragraphs.append({
            "ordinal": int(paragraph.ordinal),
            "atoms": [_atom(atom) for atom in paragraph.content.atoms],
            "reading": paragraph.content.reading,
            "natural_lines": [line.text for line in paragraph.natural_lines],
            "disposition": paragraph.disposition.value,
            "payload": sorted(kind.value for kind in paragraph.semantics.payload.kinds),
            "page_break_before": paragraph.page_break_before,
            "resolved_style": paragraph.resolved_style,
            "direct_style": paragraph.direct_style,
            "alignment": paragraph.alignment.value,
            "spacing": list(paragraph.spacing),
            "indent": list(paragraph.indent),
            "contextual_spacing": paragraph.contextual_spacing,
            "indent_departure": paragraph.indent_departure,
            "border": paragraph.border.value,
            "markers": {
                "numbered": paragraph.numbered,
                "heading": paragraph.heading,
                "thematic": paragraph.thematic,
            },
            "segment": paragraph.segment.value,
            "bold": paragraph.bold,
            "italic": paragraph.italic,
            "visual_group": (
                paragraph.visual_group.value if paragraph.visual_group is not None else None
            ),
        })
    reading = "\n".join(paragraph.content.reading for paragraph in source.paragraphs)
    return {
        "paragraphs": paragraphs,
        "readable_text_sha256": _sha256(reading),
        "styles": _json_value(source.styles),
        "layout": _json_value(source.layout),
    }


def _source_blocks(
    blocks: tuple[docx_source.SourceBlock, ...],
) -> Iterator[docx_source.SourceBlock]:
    for block in blocks:
        yield block
        match block:
            case docx_source.SourceParagraphBlock(inlines=inlines):
                yield from _inline_source_blocks(inlines)
            case docx_source.SourceTableBlock(rows=rows):
                for row in rows:
                    for cell in row.cells:
                        yield from _source_blocks(cell.blocks)
            case docx_source.SourceContentControl(blocks=children):
                yield from _source_blocks(children)
            case docx_source.SourceUnknownBlock():
                continue


def _inline_source_blocks(
    inlines: tuple[docx_source.SourceInline, ...],
) -> Iterator[docx_source.SourceBlock]:
    for inline in inlines:
        match inline:
            case (
                docx_source.SourceRun(children=children)
                | docx_source.SourceHyperlink(children=children)
                | docx_source.SourceField(children=children)
            ):
                yield from _inline_source_blocks(children)
            case docx_source.SourceTextBox(blocks=blocks):
                yield from _source_blocks(blocks)
            case _:
                continue


def _source_inlines(
    blocks: tuple[docx_source.SourceBlock, ...],
) -> Iterator[docx_source.SourceInline]:
    for block in _source_blocks(blocks):
        if not isinstance(block, docx_source.SourceParagraphBlock):
            continue
        stack = list(reversed(block.inlines))
        while stack:
            inline = stack.pop()
            yield inline
            if isinstance(
                inline,
                docx_source.SourceRun
                | docx_source.SourceHyperlink
                | docx_source.SourceField,
            ):
                stack.extend(reversed(inline.children))


def _address_key(address: docx_source.SourceAddress) -> str:
    return f"{address.story.value}:{'.'.join(map(str, address.path))}"


def _canonical_source_snapshot(
    source: docx_source.DocxSourceDocument,
) -> dict[str, object]:
    """Rich candidate source tree, kept separate from legacy-comparable facts."""
    return {
        "body": _json_value(source.body),
        "notes": _json_value(source.notes),
        "media": [
            {
                "part_name": media.part_name,
                "bytes": len(media.data),
                "sha256": hashlib.sha256(media.data).hexdigest(),
            }
            for media in source.media
        ],
        "diagnostics": _json_value(source.diagnostics),
    }


def _source_invariants(source: docx_source.DocxSourceDocument) -> dict[str, object]:
    """Assertions that make source identity and loss visible in every capture."""
    body_blocks = tuple(_source_blocks(source.body))
    linked = tuple(
        block
        for block in body_blocks
        if isinstance(block, docx_source.SourceParagraphBlock)
        and block.paragraph is not None
    )
    linked_ordinals = [
        block.paragraph.ordinal.value
        for block in linked
        if block.paragraph is not None
    ]
    addresses = [
        _address_key(block.address)
        for block in body_blocks
    ]
    for note in source.notes:
        addresses.extend(
            _address_key(block.address)
            for block in _source_blocks(note.blocks)
        )
    media_names = [media.part_name for media in source.media]
    media_name_set = set(media_names)
    all_inlines = tuple(
        _source_inlines(source.body)
    ) + tuple(
        inline
        for note in source.notes
        for inline in _source_inlines(note.blocks)
    )
    image_parts = [
        inline.media_part
        for inline in all_inlines
        if isinstance(inline, docx_source.SourceImage)
        and inline.media_part is not None
    ]
    note_refs = {
        (inline.kind, inline.note_id)
        for inline in all_inlines
        if isinstance(inline, docx_source.SourceNoteReference)
    }
    note_definitions = {(note.kind, note.note_id) for note in source.notes}
    fields: dict[int, set[str]] = {}
    for inline in all_inlines:
        if isinstance(inline, docx_source.SourceField) and inline.field_id is not None:
            fields.setdefault(inline.field_id, set()).add(inline.instruction)
    checks = {
        "body_paragraph_order": linked_ordinals == list(range(len(source.paragraphs))),
        "body_paragraph_reading": all(
            block.paragraph is not None
            and block.reading == block.paragraph.content.reading
            for block in linked
        ),
        "body_line_coordinates": all(
            block.paragraph is not None
            and block.coordinates == block.paragraph.line_coordinates
            for block in linked
        ),
        "unique_source_addresses": len(addresses) == len(set(addresses)),
        "unique_media_parts": len(media_names) == len(media_name_set),
        "image_parts_resolve": set(image_parts) <= media_name_set,
        "note_references_resolve": note_refs <= note_definitions,
        "field_identity_is_consistent": all(
            len(instructions) == 1 for instructions in fields.values()
        ),
    }
    unknown_inlines = [
        inline
        for inline in all_inlines
        if isinstance(inline, docx_source.SourceUnknownInline)
    ]
    unknown_blocks = [
        block
        for block in body_blocks
        if isinstance(block, docx_source.SourceUnknownBlock)
    ]
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "observations": {
            "body_paragraphs": len(source.paragraphs),
            "source_blocks": len(body_blocks),
            "source_addresses": len(addresses),
            "images": sum(
                isinstance(inline, docx_source.SourceImage)
                for inline in all_inlines
            ),
            "notes": len(source.notes),
            "unknown_inlines": len(unknown_inlines),
            "unknown_blocks": len(unknown_blocks),
        },
    }


def _span_value(block: ir.Block) -> object:
    return _json_value(block.source_span)


def _ir_leaves(blocks: Iterable[ir.Block], prefix: str = "body") -> Iterator[dict[str, object]]:
    """Flatten reading leaves without erasing their container order."""
    for index, block in enumerate(blocks):
        path = f"{prefix}/{index}"
        base = {"path": path, "span": _span_value(block)}
        match block:
            case ir.Heading():
                yield {**base, "kind": "heading", "text": inline_plain(block.inlines)}
            case ir.Paragraph():
                yield {**base, "kind": "paragraph", "text": inline_plain(block.inlines)}
            case ir.LineatedBlock():
                for stanza_index, stanza in enumerate(block.stanzas):
                    for line_index, line in enumerate(stanza):
                        yield {
                            "path": f"{path}/stanza/{stanza_index}/{line_index}",
                            "kind": "line",
                            "text": inline_plain(line.inlines),
                            "span": _json_value(line.span),
                        }
            case ir.QuoteBlock():
                yield from _ir_leaves(block.blocks, f"{path}/quote")
            case ir.ListBlock():
                for item_index, item in enumerate(block.items):
                    yield from _ir_leaves(item, f"{path}/item/{item_index}")
            case ir.Table():
                for row_index, row in enumerate(block.rows):
                    for cell_index, cell in enumerate(row):
                        yield {
                            "path": f"{path}/row/{row_index}/cell/{cell_index}",
                            "kind": "table-cell",
                            "text": inline_plain(cell),
                            "span": base["span"],
                        }
            case ir.ImageBlock():
                yield {**base, "kind": "image", "text": block.alt}
            case ir.CodeBlock():
                yield {**base, "kind": "code", "text": block.text}
            case ir.UnknownBlock():
                yield {**base, "kind": f"unknown:{block.note}", "text": block.text}
            case ir.Signature():
                yield {**base, "kind": "signature", "text": "\n".join(block.lines)}
            case ir.Epigraph():
                yield {
                    **base,
                    "kind": "epigraph",
                    "text": "\n".join([*block.quote, *block.footer]),
                }
            case ir.DialogueLabel():
                yield {**base, "kind": "dialogue-label", "text": block.speaker}
            case ir.ThematicBreak():
                yield {**base, "kind": "thematic-break", "text": ""}


def _ir_snapshot(document: ir.Document, media_dir: Path) -> dict[str, object]:
    leaves = list(_ir_leaves(document.blocks))
    reading = "\n".join(str(leaf["text"]) for leaf in leaves)
    footnote_leaves = [
        {
            "id": footnote.id,
            "leaves": list(_ir_leaves(footnote.blocks, f"footnote/{footnote.id}")),
        }
        for footnote in document.footnotes
    ]
    return {
        "document": _json_value(document, media_dir=media_dir),
        "leaves": leaves,
        "readable_text_sha256": _sha256(reading),
        "footnote_leaves": footnote_leaves,
    }


def _book_context(
    source: docx_source.DocxSourceDocument,
    entry: CatalogEntry,
    title_index: dict[str, Any],
    diagnostics: ir.DiagnosticSink,
) -> Context:
    policy = register_artifacts.load_register_policy_for(entry.lang)
    return Context(
        lang=entry.lang,
        demote_levels=1,
        bibliography=BibliographyLookup(title_index),
        register_policy=policy.policy,
        lineation=LineationCorrections(lineation_overrides.load_overrides(source)),
        scripture=ScripturePins(scripture_overrides.load_overrides(source)),
        rights=rights_boilerplate.plan_rights_removal(source),
        diagnostics=diagnostics,
    )


def _compile(
    source: docx_source.DocxSourceDocument,
    adapted: ir.Document,
    entry: CatalogEntry,
    title_index: dict[str, Any],
    media_dir: Path,
    adapter_diagnostics: ir.DiagnosticSink,
) -> dict[str, object]:
    if entry.kind == "poem":
        q1_diagnostics: ir.DiagnosticSink = []
        q1 = run(adapted, Context(lang=entry.lang, diagnostics=q1_diagnostics), POEM_PASSES)
        full = q1
        full_diagnostics = q1_diagnostics
    else:
        q1_diagnostics = []
        q1 = run(
            adapted,
            _book_context(source, entry, title_index, q1_diagnostics),
            BOOK_PASSES,
            until=POST_FOLD_SEAM,
        )
        full_diagnostics: ir.DiagnosticSink = []
        full = run(
            adapted,
            _book_context(source, entry, title_index, full_diagnostics),
            BOOK_PASSES,
        )

    lowered_diagnostics = [*adapter_diagnostics, *full_diagnostics]
    lowered, planned_assets = assets.plan_assets(full, media_dir, lowered_diagnostics)
    body = lower.lower(
        lowered,
        entry.lang,
        lowered_diagnostics,
        poem=entry.kind == "poem",
    )
    poem_chrome = None
    if entry.kind == "poem":
        body = docx_conversion._strip_source_duplicate_poem_title(
            body, entry.title, source.paragraphs
        )
        body, poem_chrome = docx_conversion.clean_poem_chrome(body)

    return {
        "q1": _ir_snapshot(q1, media_dir),
        "q1_diagnostics": _json_value(q1_diagnostics, media_dir=media_dir),
        "q2": _ir_snapshot(full, media_dir),
        "output": {
            "body": body,
            "body_sha256": _sha256(body),
            "bibliography": _json_value(
                docx_conversion._dedupe_bibliography(lowered.bibliography)
            ),
            "cross_refs": _json_value(
                cross_refs.extract_cross_refs(body, entry.work_key, title_index)
            ),
            "assets": _json_value(planned_assets, media_dir=media_dir),
            "diagnostics": _json_value(lowered_diagnostics, media_dir=media_dir),
            "poem_chrome": _json_value(poem_chrome),
        },
    }


def _entry_map(entries: list[CatalogEntry]) -> dict[Path, CatalogEntry]:
    return {
        (entry.work_dir / f"{entry.lang}.docx").resolve(): entry
        for entry in entries
    }


def _snapshot_path(output: Path, docx: Path) -> Path:
    relative = docx.resolve().relative_to(ROOT)
    return output / relative.with_suffix(relative.suffix + ".json.gz")


def _canonical_source_path(output: Path, docx: Path) -> Path:
    relative = docx.resolve().relative_to(ROOT)
    return (
        output
        / CANONICAL_SOURCE_DIR
        / relative.with_suffix(relative.suffix + ".json.gz")
    )


def _write_snapshot(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=6) as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        stream.write("\n")


def _read_snapshot(path: Path) -> Snapshot:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        value: object = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"snapshot root must be an object: {path}")
    return cast(Snapshot, value)


def _capture_one(
    docx: Path,
    entry: CatalogEntry,
    title_index: dict[str, Any],
    output: Path,
) -> None:
    started = time.perf_counter()
    tracemalloc.start()
    source_started = time.perf_counter()
    source = docx_source.read(docx)
    source_seconds = time.perf_counter() - source_started

    with tempfile.TemporaryDirectory(prefix="pancratius-reader-parity-") as raw_media:
        media_dir = Path(raw_media)
        diagnostics: ir.DiagnosticSink = []
        adapter_started = time.perf_counter()
        adapted = docx_adapter.adapt(source, media_dir, diagnostics)
        adapter_seconds = time.perf_counter() - adapter_started
        compile_started = time.perf_counter()
        compiler = _compile(
            source, adapted, entry, title_index, media_dir, diagnostics
        )
        compile_seconds = time.perf_counter() - compile_started
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        candidate_payload: dict[str, object] = {}
        # The characterization commit runs against both sides of the switch. The
        # legacy aggregate has no rich body tree; candidate-only coverage evidence
        # appears as soon as the replacement aggregate exposes one.
        if hasattr(source, "body"):
            canonical_source_path = _canonical_source_path(output, docx)
            _write_snapshot(canonical_source_path, _canonical_source_snapshot(source))
            candidate_payload = {
                "canonical_source_artifact": canonical_source_path.relative_to(
                    output
                ).as_posix(),
                "source_invariants": _source_invariants(source),
            }
        snapshot = {
            "schema": SCHEMA,
            "source_path": docx.resolve().relative_to(ROOT).as_posix(),
            "source": _source_snapshot(source),
            **candidate_payload,
            "adapted": _ir_snapshot(adapted, media_dir),
            "adapter_diagnostics": _json_value(diagnostics, media_dir=media_dir),
            **compiler,
            "metrics": {
                "source_seconds": source_seconds,
                "adapter_seconds": adapter_seconds,
                "compiler_seconds": compile_seconds,
                "total_seconds": time.perf_counter() - started,
                "python_peak_bytes": peak,
                "docx_bytes": docx.stat().st_size,
            },
        }
        _write_snapshot(_snapshot_path(output, docx), snapshot)


def _selected_docx(args: argparse.Namespace) -> list[Path]:
    if args.all:
        return sorted(CONTENT_ROOT.glob("**/*.docx"))
    if not args.docx:
        raise SystemExit("capture needs DOCX paths or --all")
    return [Path(value).resolve() for value in args.docx]


def capture(args: argparse.Namespace) -> int:
    entries = scan_catalog(CONTENT_ROOT)
    by_docx = _entry_map(entries)
    title_index = build_title_index(entries)
    failures = 0
    selected = _selected_docx(args)
    for index, docx in enumerate(selected, start=1):
        try:
            entry = by_docx[docx.resolve()]
        except KeyError:
            print(f"{docx}: no matching catalog entry", file=sys.stderr)
            failures += 1
            continue
        print(
            f"[{index}/{len(selected)}] {docx.resolve().relative_to(ROOT)}",
            file=sys.stderr,
            flush=True,
        )
        try:
            _capture_one(docx, entry, title_index, args.output)
        except Exception as exc:  # keep a full-corpus run useful after one bad source
            failures += 1
            print(f"  ERROR {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            if args.fail_fast:
                raise
    return int(bool(failures))


def _first_difference(left: object, right: object, path: str = "$") -> str | None:
    if type(left) is not type(right):
        return f"{path}: {type(left).__name__} != {type(right).__name__}"
    if isinstance(left, dict) and isinstance(right, dict):
        left_keys = set(left)
        right_keys = set(right)
        if left_keys != right_keys:
            return f"{path}: keys -{sorted(left_keys - right_keys)} +{sorted(right_keys - left_keys)}"
        for key in sorted(left):
            if difference := _first_difference(left[key], right[key], f"{path}.{key}"):
                return difference
        return None
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return f"{path}: length {len(left)} != {len(right)}"
        for index, (left_item, right_item) in enumerate(zip(left, right, strict=True)):
            if difference := _first_difference(
                left_item, right_item, f"{path}[{index}]"
            ):
                return difference
        return None
    if left != right:
        def short(value: object) -> str:
            rendered = repr(value)
            if len(rendered) <= 240:
                return rendered
            digest = _sha256(rendered)[:12]
            return f"{rendered[:200]}… <len={len(rendered)} sha256={digest}>"

        return f"{path}: {short(left)} != {short(right)}"
    return None


def _reading_leaves(snapshot: Snapshot) -> object:
    leaves = snapshot["leaves"]
    assert isinstance(leaves, list)
    return [
        {"kind": leaf["kind"], "text": leaf["text"]}
        for leaf in leaves
        if leaf["text"]
    ]


def _reading_footnotes(snapshot: Snapshot) -> object:
    footnotes = snapshot["footnote_leaves"]
    assert isinstance(footnotes, list)
    return [
        {
            "id": footnote["id"],
            "leaves": _reading_leaves({"leaves": footnote["leaves"]}),
        }
        for footnote in footnotes
    ]


def _reading_stream(snapshot: Snapshot) -> object:
    """Ordered readable text, insensitive to semantic block split/merge choices."""

    def normalized(leaves: object) -> str:
        assert isinstance(leaves, list)
        text = (
            cast(Snapshot, leaf).get("text")
            for leaf in leaves
            if isinstance(leaf, dict)
        )
        return re.sub(
            r"\s+",
            " ",
            " ".join(str(value) for value in text if value),
        ).strip()

    footnotes = snapshot["footnote_leaves"]
    assert isinstance(footnotes, list)
    return {
        "body": normalized(snapshot["leaves"]),
        "footnotes": [
            {"id": footnote["id"], "text": normalized(footnote["leaves"])}
            for footnote in footnotes
        ],
    }


_IR_NON_SEMANTIC_FIELDS = frozenset({
    "source_span",
    "span",
    "facts",
    "evidence",
    "lineation_repairs",
    "raw",
    "shape",
    "asset_id",
    "field_id",
})


def _ir_semantics(snapshot: Snapshot) -> object:
    """Rich/container semantics without frontend-private coordinates or evidence."""

    def project(value: object) -> object:
        if isinstance(value, dict):
            mapping = cast(Snapshot, value)
            return {
                key: project(item)
                for key, item in mapping.items()
                if key not in _IR_NON_SEMANTIC_FIELDS
            }
        if isinstance(value, list):
            projected = [project(item) for item in value]
            normalized: list[object] = []
            for item in projected:
                item_mapping = cast(Snapshot, item) if isinstance(item, dict) else None
                previous_mapping = (
                    cast(Snapshot, normalized[-1])
                    if normalized and isinstance(normalized[-1], dict)
                    else None
                )
                if (
                    item_mapping is not None
                    and item_mapping.get("$type") == "Text"
                    and item_mapping.get("value") == ""
                ):
                    continue
                if (
                    item_mapping is not None
                    and previous_mapping is not None
                    and item_mapping.get("$type") == "Text"
                    and previous_mapping.get("$type") == "Text"
                ):
                    previous_mapping["value"] = (
                        str(previous_mapping["value"])
                        + str(item_mapping["value"])
                    )
                    continue
                item_children = (
                    item_mapping.get("children")
                    if item_mapping is not None
                    else None
                )
                previous_children = (
                    previous_mapping.get("children")
                    if previous_mapping is not None
                    else None
                )
                if (
                    item_mapping is not None
                    and previous_mapping is not None
                    and item_mapping.get("$type") in {
                        "DirectionalSpan", "Emphasis", "Link", "Quoted",
                    }
                    and item_mapping.get("$type") == previous_mapping.get("$type")
                    and {
                        key: nested
                        for key, nested in item_mapping.items()
                        if key != "children"
                    }
                    == {
                        key: nested
                        for key, nested in previous_mapping.items()
                        if key != "children"
                    }
                    and isinstance(item_children, list)
                    and isinstance(previous_children, list)
                ):
                    previous_mapping["children"] = project([
                        *previous_children,
                        *item_children,
                    ])
                    continue
                normalized.append(item)
            return normalized
        return value

    return project(snapshot["document"])


def compare(args: argparse.Namespace) -> int:
    old_files = {
        path.relative_to(args.old): path
        for path in args.old.glob("**/*.json.gz")
        if CANONICAL_SOURCE_DIR not in path.relative_to(args.old).parts
    }
    new_files = {
        path.relative_to(args.new): path
        for path in args.new.glob("**/*.json.gz")
        if CANONICAL_SOURCE_DIR not in path.relative_to(args.new).parts
    }
    report: dict[str, Any] = {
        "missing": sorted(path.as_posix() for path in old_files.keys() - new_files.keys()),
        "added": sorted(path.as_posix() for path in new_files.keys() - old_files.keys()),
        "files": [],
    }
    for relative in sorted(old_files.keys() & new_files.keys()):
        old = _read_snapshot(old_files[relative])
        new = _read_snapshot(new_files[relative])
        old_metrics = old.pop("metrics", {})
        new_metrics = new.pop("metrics", {})
        assert isinstance(old_metrics, dict) and isinstance(new_metrics, dict)

        def comparison_result(left: object, right: object) -> dict[str, object]:
            return {
                "equal": (difference := _first_difference(left, right)) is None,
                **({"first_difference": difference} if difference else {}),
            }

        # Compute one projection pair at a time. Holding adapted, Q1, and Q2
        # projections together makes the largest book need multiples of its
        # actual comparison memory.
        results = {
            "source_facts": comparison_result(old["source"], new["source"]),
            "adapted_text_stream": comparison_result(
                _reading_stream(old["adapted"]),
                _reading_stream(new["adapted"]),
            ),
            "adapted_reading": comparison_result(
                {
                    "leaves": _reading_leaves(old["adapted"]),
                    "footnotes": _reading_footnotes(old["adapted"]),
                },
                {
                    "leaves": _reading_leaves(new["adapted"]),
                    "footnotes": _reading_footnotes(new["adapted"]),
                },
            ),
        }
        results["adapted_semantics"] = comparison_result(
            _ir_semantics(old["adapted"]),
            _ir_semantics(new["adapted"]),
        )
        results["q1_reading"] = comparison_result(
            _reading_leaves(old["q1"]), _reading_leaves(new["q1"])
        )
        results["q1_text_stream"] = comparison_result(
            _reading_stream(old["q1"]), _reading_stream(new["q1"])
        )
        results["q1_semantics"] = comparison_result(
            _ir_semantics(old["q1"]), _ir_semantics(new["q1"])
        )
        results["q2_reading"] = comparison_result(
            _reading_leaves(old["q2"]), _reading_leaves(new["q2"])
        )
        results["q2_text_stream"] = comparison_result(
            _reading_stream(old["q2"]), _reading_stream(new["q2"])
        )
        results["q2_semantics"] = comparison_result(
            _ir_semantics(old["q2"]), _ir_semantics(new["q2"])
        )
        results["markdown"] = comparison_result(
            old["output"]["body"], new["output"]["body"]
        )
        results["bibliography"] = comparison_result(
            old["output"]["bibliography"], new["output"]["bibliography"]
        )
        results["cross_refs"] = comparison_result(
            old["output"]["cross_refs"], new["output"]["cross_refs"]
        )
        results["assets"] = comparison_result(
            old["output"]["assets"], new["output"]["assets"]
        )
        results["adapter_diagnostics"] = comparison_result(
            old["adapter_diagnostics"], new["adapter_diagnostics"]
        )
        results["compiler_diagnostics"] = comparison_result(
            old["output"]["diagnostics"], new["output"]["diagnostics"]
        )

        timings: dict[str, object] = {}
        for name in ("source_seconds", "adapter_seconds", "compiler_seconds", "total_seconds", "python_peak_bytes"):
            old_value = old_metrics.get(name)
            new_value = new_metrics.get(name)
            if not isinstance(old_value, int | float) or not isinstance(new_value, int | float):
                continue
            timings[name] = {
                "old": old_value,
                "new": new_value,
                "new_over_old": new_value / old_value if old_value else None,
            }
        component_names = ("source_seconds", "adapter_seconds", "compiler_seconds")
        old_pipeline = [old_metrics.get(name) for name in component_names]
        new_pipeline = [new_metrics.get(name) for name in component_names]
        if all(isinstance(value, int | float) for value in (*old_pipeline, *new_pipeline)):
            old_pipeline_seconds = sum(
                value for value in old_pipeline if isinstance(value, int | float)
            )
            new_pipeline_seconds = sum(
                value for value in new_pipeline if isinstance(value, int | float)
            )
            timings["pipeline_seconds"] = {
                "old": old_pipeline_seconds,
                "new": new_pipeline_seconds,
                "new_over_old": (
                    new_pipeline_seconds / old_pipeline_seconds
                    if old_pipeline_seconds
                    else None
                ),
            }

        item = {
            "path": relative.as_posix(),
            "checks": results,
            "candidate_source_invariants": new.get("source_invariants"),
            "metrics": timings,
        }
        assert isinstance(report["files"], list)
        report["files"].append(item)

    files = report["files"]
    assert isinstance(files, list)
    report["summary"] = {
        name: {
            "equal": sum(1 for item in files if item["checks"][name]["equal"]),
            "different": sum(1 for item in files if not item["checks"][name]["equal"]),
        }
        for name in (
            "source_facts", "adapted_text_stream", "adapted_reading",
            "adapted_semantics", "q1_text_stream", "q1_reading",
            "q1_semantics", "q2_text_stream", "q2_reading", "q2_semantics",
            "markdown", "bibliography", "cross_refs", "assets",
            "adapter_diagnostics", "compiler_diagnostics",
        )
    }
    candidate_invariants = [
        item.get("candidate_source_invariants")
        for item in files
    ]
    report["candidate_source_invariants"] = {
        "passed": sum(
            isinstance(invariants, dict) and invariants.get("passed") is True
            for invariants in candidate_invariants
        ),
        "failed": sum(
            isinstance(invariants, dict) and invariants.get("passed") is False
            for invariants in candidate_invariants
        ),
        "unavailable": sum(invariants is None for invariants in candidate_invariants),
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    if not args.quiet:
        print(rendered, end="")
    semantic_failure = any(
        not item["checks"][name]["equal"]
        for item in files
        for name in (
            "source_facts", "adapted_text_stream", "adapted_reading",
            "adapted_semantics", "q1_text_stream", "q1_reading",
            "q1_semantics", "q2_text_stream", "q2_reading", "q2_semantics",
        )
    )
    invariant_failure = any(
        isinstance(invariants, dict) and invariants.get("passed") is False
        for invariants in candidate_invariants
    )
    return int(bool(
        report["missing"]
        or report["added"]
        or semantic_failure
        or invariant_failure
    ))


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    subcommands = command.add_subparsers(dest="command", required=True)

    capture_parser = subcommands.add_parser("capture")
    capture_parser.add_argument("docx", nargs="*")
    capture_parser.add_argument("--all", action="store_true")
    capture_parser.add_argument("--output", type=Path, required=True)
    capture_parser.add_argument("--fail-fast", action="store_true")
    capture_parser.set_defaults(run=capture)

    compare_parser = subcommands.add_parser("compare")
    compare_parser.add_argument("old", type=Path)
    compare_parser.add_argument("new", type=Path)
    compare_parser.add_argument("--report", type=Path)
    compare_parser.add_argument("--quiet", action="store_true")
    compare_parser.set_defaults(run=compare)
    return command


def main() -> int:
    args = parser().parse_args()
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
