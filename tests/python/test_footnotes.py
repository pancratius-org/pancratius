"""Final footnote diagnostics and the FATAL-on-unresolved-ref boundary."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from pancratius import footnotes
from pancratius.writeplan import (
    Diagnostic,
    EnsureDirOp,
    WritePlan,
    WriteTextOp,
    has_fatal,
)
from pancratius.writer import apply as apply_plan

# ---------------------------------------------------------------------------
# analyze_footnotes
# ---------------------------------------------------------------------------

def test_clean_doc_has_no_diagnostics() -> None:
    body = "A claim.[^1]\n\n[^1]: The footnote.\n"
    assert footnotes.analyze_footnotes(body) == []


def test_orphaned_reference_is_fatal() -> None:
    body = "A claim.[^1] and another.[^2]\n\n[^1]: only one is defined.\n"
    diags = footnotes.analyze_footnotes(body)
    fatal = [d for d in diags if d.severity == "fatal"]
    assert len(fatal) == 1
    assert fatal[0].code == "import.footnote-unresolved"
    assert "[^2]" in fatal[0].message


def test_unused_definition_is_warning() -> None:
    body = "A claim.[^1]\n\n[^1]: used.\n\n[^2]: never referenced.\n"
    diags = footnotes.analyze_footnotes(body)
    assert has_warning(diags, "import.footnote-unused", "[^2]")
    codes = {(d.severity, d.code) for d in diags}
    assert ("warning", "import.footnote-unused") in codes
    assert ("fatal", "import.footnote-unresolved") not in codes


def test_duplicate_definition_is_warning() -> None:
    body = "A claim.[^1]\n\n[^1]: first.\n\n[^1]: second (duplicate).\n"
    diags = footnotes.analyze_footnotes(body)
    dup = [d for d in diags if d.code == "import.footnote-duplicate"]
    assert len(dup) == 1
    assert dup[0].severity == "warning"
    assert "[^1]" in dup[0].message
    # The id IS referenced and IS defined, so no unresolved/unused for it.
    assert not [d for d in diags if d.code == "import.footnote-unresolved"]


def test_no_footnotes_at_all_is_clean() -> None:
    assert footnotes.analyze_footnotes("Plain prose, no notes.\n") == []


def has_warning(diags: list[footnotes.FootnoteDiagnostic], code: str, needle: str) -> bool:
    return any(d.severity == "warning" and d.code == code and needle in d.message for d in diags)


# ---------------------------------------------------------------------------
# the FATAL actually blocks the writer
# ---------------------------------------------------------------------------

def test_orphaned_ref_fatal_blocks_writer(tmp_path: Path) -> None:
    """A body with a genuinely orphaned ref yields a fatal diagnostic; carried
    into the plan, the writer REFUSES and writes nothing."""
    body = "A claim with no definition anywhere.[^9]\n"
    diags = footnotes.analyze_footnotes(body)
    assert has_fatal(Diagnostic(d.severity, d.code, d.message) for d in diags)

    content_root = tmp_path / "src" / "content"
    scope = PurePosixPath("books") / "99-orphan"
    plan = WritePlan(
        target_root=content_root,
        target_scope=scope,
        operations=(
            EnsureDirOp(
                rel_path=scope,
                role="canonical_source",
                reason="bundle directory",
            ),
            WriteTextOp(
                rel_path=scope / "ru.md",
                role="canonical_source",
                reason="body",
                content=body,
            ),
        ),
        diagnostics=tuple(
            Diagnostic(d.severity, d.code, d.message) for d in diags
        ),
        replace=False,
    )
    report = apply_plan(plan, dry_run=False)

    assert report.refused, "writer must refuse on the orphaned-footnote fatal"
    assert not report.created and not report.changed
    assert not (content_root / "books" / "99-orphan" / "ru.md").exists()
    assert any(
        d.severity == "fatal" and d.code == "import.footnote-unresolved"
        for d in report.diagnostics
    )
