from __future__ import annotations

from typing import assert_never

from pancratius.translation.docx.models import DocxTranslationBatch, DocxTranslationDiscovery


def print_batch(batch: DocxTranslationBatch, *, dry_run: bool) -> None:
    write_verb = "would write" if dry_run else "wrote"
    if not batch.reports:
        print(f"nothing to translate to DOCX. {_discovery_summary(batch.discovery)}")
        return
    for report in batch.reports:
        rel = report.target.translated_docx
        wr = report.write_report
        refused = bool(wr.refused) or any(diagnostic.severity == "fatal" for diagnostic in wr.diagnostics)
        status = "REFUSE" if refused else "OK"
        verb = "would refuse" if dry_run and refused else "refused" if refused else write_verb
        if report.backend == "transfer":
            backend_note = f"{report.aligned_units}/{report.source_units} aligned units"
        elif report.backend == "markdown-render":
            backend_note = f"{report.translated_units} rendered Markdown block(s)"
        else:
            assert_never(report.backend)
        print(
            f"  {status} book-{report.target.number:02d}: {verb} {rel} "
            f"({report.backend}; {backend_note})"
        )
        for diag in wr.diagnostics:
            if diag.severity in {"fatal", "warning"}:
                print(f"      {diag.severity}: [{diag.code}] {diag.message}")
    print(_discovery_summary(batch.discovery))


def _discovery_summary(discovery: DocxTranslationDiscovery) -> str:
    return (
        f"coverage: {discovery.eligible}/{discovery.source_books} source book(s) eligible; "
        f"{discovery.missing} missing, {discovery.existing} existing source DOCX, "
        f"{discovery.ineligible} ineligible."
    )
