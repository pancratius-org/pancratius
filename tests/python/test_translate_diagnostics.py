"""The diagnostics locate the characteristic chunked-translation defects and tag
each by chunk-seam proximity."""

from __future__ import annotations

from pancratius.translate.checks import Finding, Severity
from pancratius.translate.chunker import plan_chunks
from pancratius.translate.config import TranslateConfig
from pancratius.translate.diagnostics import (
    BookAudit,
    FindingKind,
    audit_book,
    build_digest,
    inconsistent_term_seams,
    seam_indices,
    seam_windows,
)
from pancratius.translate.document import parse_document


def test_seam_indices_mark_chunk_boundaries() -> None:
    doc = parse_document("один.\n\nдва.\n\nтри.\n\nчетыре.\n")
    cfg = TranslateConfig(chunk_source_tokens=2, source_chars_per_token=1.0, chunk_max_units=1)
    seams = seam_indices(doc, cfg)
    # One unit per chunk -> every boundary is a seam; interior units all flagged.
    assert seams  # boundaries exist
    assert max(seams) < len(doc.units)


def test_detects_adjacent_duplicate() -> None:
    src = parse_document("Свет.\n\nТьма.\n")
    tgt = parse_document("Same.\n\nSame.\n")  # different sources, identical translations
    audit = audit_book(src, tgt, TranslateConfig(), book_key="t")
    assert any(f.kind is FindingKind.DUPLICATE_ADJACENT for f in audit.findings)


def test_detects_dropped_content() -> None:
    src = parse_document(
        "Это очень длинное и обстоятельное предложение, в котором много смысла, "
        "слов и образов, и оно явно несёт значительное содержание.\n"
    )
    tgt = parse_document("Short.\n")  # near-empty rendering of a 100+ char line
    audit = audit_book(src, tgt, TranslateConfig(), book_key="t")
    assert any(f.kind is FindingKind.DROPPED_CONTENT for f in audit.findings)


def test_residual_cyrillic_run_is_high_lone_letter_is_medium() -> None:
    from pancratius.translate.checks import Severity
    src = parse_document("первый\n\nвторой\n")
    tgt = parse_document("the letter Я here\n\nполностью непереведено здесь\n")
    audit = audit_book(src, tgt, TranslateConfig(), book_key="t")
    cyr = {f.severity for f in audit.findings if f.kind is FindingKind.RESIDUAL_CYRILLIC}
    assert Severity.HIGH in cyr  # the untranslated run
    assert Severity.MEDIUM in cyr  # the lone letter mention


def test_clean_translation_has_no_findings() -> None:
    src = parse_document("Свет мой.\n\nТьма моя.\n")
    tgt = parse_document("My light.\n\nMy darkness.\n")
    assert audit_book(src, tgt, TranslateConfig(), book_key="t").findings == ()


# --- seam reconcile -----------------------------------------------------------
_SMALL = TranslateConfig(chunk_source_tokens=2, source_chars_per_token=1.0, chunk_max_units=1)


def test_seam_window_straddles_the_boundary() -> None:
    doc = parse_document("один.\n\nдва.\n\nтри.\n\nчетыре.\n")
    chunks = plan_chunks(doc, _SMALL)
    seams = seam_windows(doc, chunks, k=1)
    # One unit per chunk, k=1 -> each window is exactly the tail of A and head of B.
    assert seams
    first = seams[0]
    assert [u.id for u in first.window] == [doc.units[0].id, doc.units[1].id]


def test_inconsistent_term_seam_flags_only_the_divergent_side() -> None:
    # "Свет" appears in three units; rendered "Light" twice and dropped once. The
    # missing rendering's seam window is flagged; consistent occurrences are not.
    doc = parse_document("Свет один.\n\nСвет два.\n\nСвет три.\n")
    chunks = plan_chunks(doc, _SMALL)
    seams = seam_windows(doc, chunks, k=1)
    a, b, c = (u.id for u in doc.units)
    translations = {a: "Light one.", b: "Light two.", c: "Glow three."}
    flagged = inconsistent_term_seams(doc, translations, seams, terms=[("Свет", "Light")])
    # The divergent unit (c) lives in the last seam window; at least one seam fires,
    # and a uniformly-rendered term fires none.
    assert flagged
    uniform = inconsistent_term_seams(
        doc, {a: "Light one.", b: "Light two.", c: "Light three."}, seams, terms=[("Свет", "Light")]
    )
    assert uniform == set()


def test_inconsistent_term_scan_ignores_single_occurrence_terms() -> None:
    doc = parse_document("Свет один.\n\nТьма два.\n")
    seams = seam_windows(doc, plan_chunks(doc, _SMALL), k=1)
    a, b = (u.id for u in doc.units)
    flagged = inconsistent_term_seams(doc, {a: "Light.", b: "Dark."}, seams, terms=[("Свет", "Light")])
    assert flagged == set()


# --- end-of-run digest --------------------------------------------------------
def _book_audit(book_key: str = "book-1") -> BookAudit:
    src = parse_document("Свет.\n\nТьма.\n")
    tgt = parse_document("Light.\n\nDark.\n")
    return audit_book(src, tgt, TranslateConfig(), book_key=book_key)


def test_digest_is_empty_when_nothing_actionable() -> None:
    assert build_digest(_book_audit(), warnings=[]) == ()


def test_digest_groups_and_caps_warnings() -> None:
    warnings = [
        Finding(Severity.MEDIUM, "frontmatter_cyrillic", "Cyrillic remains in frontmatter title"),
        *[Finding(Severity.MEDIUM, "mixed_script", "welded token", f"u{i:04d}") for i in range(8)],
    ]
    lines = build_digest(_book_audit("book-9"), warnings=warnings)
    text = "\n".join(lines)
    assert "diagnostics for book-9" in text
    assert "frontmatter (1)" in text
    assert "mixed-script (8)" in text
    assert "+2 more" in text  # 8 mixed-script, capped at 6 shown
