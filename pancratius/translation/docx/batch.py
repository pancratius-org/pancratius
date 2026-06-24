from __future__ import annotations

import tempfile
from contextlib import ExitStack
from pathlib import Path, PurePosixPath
from typing import assert_never

from pancratius.content_catalog import scan_catalog
from pancratius.docx_roundtrip import (
    DocxRoundTripError,
    DocxRoundTripFinding,
    staged_docx_roundtrip_workspace,
)
from pancratius.kinds import SEGMENT_OF
from pancratius.locales import DEFAULT_LOCALE, Locale
from pancratius.paths import CONTENT_ROOT
from pancratius.translation.docx.models import (
    BookDocxTranslationTarget,
    DocxTranslationBackend,
    DocxTranslationBatch,
    DocxTranslationDiscovery,
    DocxTranslationError,
    DocxTranslationReport,
)
from pancratius.translation.docx.transfer import render_markdown_docx, render_translated_docx
from pancratius.writeplan import CopyOp, Diagnostic, EnsureDirOp, WritePlan
from pancratius.writer import apply as apply_plan


def discover_targets(
    *,
    content_root: Path = CONTENT_ROOT,
    book: int | None = None,
    lang: Locale = "en",
    include_existing: bool = False,
) -> tuple[BookDocxTranslationTarget, ...]:
    targets, _discovery = _discover_targets(
        content_root=content_root,
        book=book,
        lang=lang,
        include_existing=include_existing,
    )
    return targets


def _discover_targets(
    *,
    content_root: Path = CONTENT_ROOT,
    book: int | None = None,
    lang: Locale = "en",
    include_existing: bool = False,
) -> tuple[tuple[BookDocxTranslationTarget, ...], DocxTranslationDiscovery]:
    if lang == DEFAULT_LOCALE:
        raise DocxTranslationError(
            f"docx translate-from-md refuses source locale {DEFAULT_LOCALE!r}; "
            "choose a translated target locale."
        )
    catalog = scan_catalog(content_root)
    translated_entries = {
        entry.number: entry
        for entry in catalog
        if entry.kind == "book" and entry.lang == lang
    }
    source_entries = sorted(
        (entry for entry in catalog if entry.kind == "book" and entry.lang == DEFAULT_LOCALE),
        key=lambda entry: entry.number,
    )
    targets: list[BookDocxTranslationTarget] = []
    source_books = 0
    eligible = 0
    missing = 0
    existing = 0
    ineligible = 0
    matched_source = False
    explicit_ineligible_detail = ""

    for entry in source_entries:
        if book is not None and entry.number != book:
            continue
        matched_source = True
        source_books += 1
        source_md = entry.work_dir / "ru.md"
        source_docx = entry.work_dir / "ru.docx"
        translated_md = entry.work_dir / f"{lang}.md"
        translated_docx = entry.work_dir / f"{lang}.docx"
        missing_inputs = [
            path.name
            for path in (source_docx, source_md, translated_md)
            if not path.is_file()
        ]
        if entry.number not in translated_entries:
            missing_inputs.append(f"{lang}.md catalog entry")
        if missing_inputs:
            ineligible += 1
            explicit_ineligible_detail = ", ".join(dict.fromkeys(missing_inputs))
            continue
        eligible += 1
        translated_exists = translated_docx.is_file()
        if translated_exists:
            existing += 1
        else:
            missing += 1

        should_include = not translated_exists
        if translated_exists and (book is not None or include_existing):
            should_include = True
        if not should_include:
            continue
        targets.append(BookDocxTranslationTarget(
            source_entry=entry,
            translated_entry=translated_entries[entry.number],
            source_docx=source_docx,
            source_md=source_md,
            translated_md=translated_md,
            translated_docx=translated_docx,
        ))
    discovery = DocxTranslationDiscovery(
        source_books=source_books,
        eligible=eligible,
        missing=missing,
        existing=existing,
        ineligible=ineligible,
    )
    if book is not None and not targets:
        if not matched_source:
            raise DocxTranslationError(f"book-{book:02d} was not found in {content_root / 'books'}")
        detail = explicit_ineligible_detail or f"eligible {lang}.md book entry"
        raise DocxTranslationError(
            f"book-{book:02d} cannot be translated to {lang}.docx: missing {detail}."
        )
    return tuple(sorted(targets, key=lambda target: target.number)), discovery


def _target_scope(target: BookDocxTranslationTarget) -> PurePosixPath:
    segment = SEGMENT_OF[target.source_entry.kind]
    return PurePosixPath(segment) / target.source_entry.work_dir.name


def _roundtrip_diagnostics(findings: tuple[DocxRoundTripFinding, ...]) -> tuple[Diagnostic, ...]:
    return tuple(
        Diagnostic(
            finding.severity,
            finding.code,
            finding.message,
        )
        for finding in findings
        if finding.severity != "info"
    )


def translate_docx_batch(
    *,
    content_root: Path = CONTENT_ROOT,
    book: int | None = None,
    lang: Locale = "en",
    dry_run: bool = False,
    replace: bool = False,
    limit: int = 0,
    backend: DocxTranslationBackend = "transfer",
) -> DocxTranslationBatch:
    if backend not in ("transfer", "markdown-render"):
        raise DocxTranslationError(f"unknown translated DOCX backend: {backend}")
    if replace and book is None:
        raise DocxTranslationError(
            "--replace requires an explicit book:NN selector; existing translated DOCX is source."
        )
    if limit < 0:
        raise DocxTranslationError("--limit must be non-negative.")
    if book is not None and limit:
        raise DocxTranslationError("--limit cannot be combined with an explicit book:NN selector.")
    discovered_targets, discovery = _discover_targets(
        content_root=content_root,
        book=book,
        lang=lang,
        include_existing=replace,
    )
    targets = list(discovered_targets)
    if limit:
        targets = targets[:limit]
    reports: list[DocxTranslationReport] = []
    with ExitStack() as stack:
        roundtrip_workspace = None
        for target in targets:
            diagnostics: list[Diagnostic] = []
            if target.translated_docx.exists() and not replace:
                diagnostics.append(Diagnostic(
                    "fatal",
                    "docx-translate.overwrite-refused",
                    (
                        f"{target.translated_docx} exists; after bootstrap, translated DOCX is "
                        "source. Pass --replace with this explicit book only if you intend to "
                        "discard DOCX-side edits."
                    ),
                ))
                plan = WritePlan(
                    target_root=content_root,
                    target_scope=_target_scope(target),
                    operations=(EnsureDirOp(_target_scope(target), "source_artifact", "work bundle"),),
                    diagnostics=tuple(diagnostics),
                    replace=replace,
                    source_document=target.source_docx,
                )
                reports.append(DocxTranslationReport(
                    target=target,
                    write_report=apply_plan(plan, dry_run=True),
                    source_units=0,
                    translated_units=0,
                    aligned_units=0,
                ))
                continue

            with tempfile.TemporaryDirectory(prefix="pancratius-docx-translate-") as tmp:
                staged = Path(tmp) / target.translated_docx.name
                if backend == "transfer":
                    source_units, translated_units, aligned_units, transfer_diags = render_translated_docx(
                        source_docx=target.source_docx,
                        source_md=target.source_md,
                        translated_md=target.translated_md,
                        out=staged,
                    )
                elif backend == "markdown-render":
                    source_units, translated_units, aligned_units, transfer_diags = render_markdown_docx(
                        target=target,
                        lang=lang,
                        out=staged,
                    )
                else:
                    assert_never(backend)
                diagnostics.extend(transfer_diags)
                if not staged.exists():
                    diagnostics.append(Diagnostic(
                        "fatal",
                        "docx-translate.missing-staged-docx",
                        f"{backend} did not produce {staged.name}.",
                    ))
                else:
                    if roundtrip_workspace is None:
                        roundtrip_workspace = stack.enter_context(staged_docx_roundtrip_workspace(
                            content_root=content_root,
                        ))
                    try:
                        roundtrip = roundtrip_workspace.check(
                            entry=target.translated_entry,
                            md_path=target.translated_md,
                            docx_path=staged,
                            lang=lang,
                        )
                    except DocxRoundTripError as exc:
                        diagnostics.append(Diagnostic(
                            "fatal",
                            "docx-translate.roundtrip-check-failed",
                            str(exc),
                        ))
                    else:
                        diagnostics.extend(_roundtrip_diagnostics(roundtrip.findings))
                scope = _target_scope(target)
                rel_docx = scope / target.translated_docx.name
                operations: list[EnsureDirOp | CopyOp] = [
                    EnsureDirOp(scope, "source_artifact", "work bundle"),
                ]
                if staged.exists() and not any(diagnostic.severity == "fatal" for diagnostic in diagnostics):
                    operations.append(CopyOp(
                        rel_path=rel_docx,
                        role="source_artifact",
                        reason=f"translated DOCX from {target.source_docx.name}, {target.source_md.name}, and {target.translated_md.name}",
                        source=staged,
                    ))
                plan = WritePlan(
                    target_root=content_root,
                    target_scope=scope,
                    operations=tuple(operations),
                    diagnostics=tuple(diagnostics),
                    replace=replace,
                    source_document=target.source_docx,
                )
                write_report = apply_plan(plan, dry_run=dry_run)
                reports.append(DocxTranslationReport(
                    target=target,
                    write_report=write_report,
                    source_units=source_units,
                    translated_units=translated_units,
                    aligned_units=aligned_units,
                    backend=backend,
                    output=target.translated_docx,
                ))
    return DocxTranslationBatch(tuple(reports), discovery=discovery)
