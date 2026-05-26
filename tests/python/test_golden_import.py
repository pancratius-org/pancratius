"""Golden test net: freeze the CURRENT DOCX->Markdown importer behavior.

The single job of this module is to lock down what the live import path
(`import_docx.import_work` -> `pancratius.docx_conversion.convert_single_docx`, the typed-IR
pipeline) produces, so refactors stay behavior-preserving against a committed
snapshot.

What is frozen, per case, under ``tests/golden/<case>/``:
  * ``body.md``          — the markdown body (everything AFTER the frontmatter).
  * ``frontmatter.yaml`` — the frontmatter dict reduced to STRUCTURAL keys
                           (kind/number/slug/title/lang/tags/cover/translation/
                           cross_refs/date/description). Volatile or
                           host/time/run-dependent keys are dropped — there are
                           none in the current output, but the allowlist makes
                           that guarantee explicit and future-proof.
  * ``assets.txt``       — sorted list of ``images/**`` filenames produced.
  * ``bibliography.yaml``— copied verbatim if the import produced one.

Deliberate-update path:
  Run with ``GOLDEN_UPDATE=1`` to (re)generate every golden file instead of
  asserting. Phase 4 will use this to update the footnote goldens with a
  reviewed diff once the orphaned-``[^N]`` bug is fixed. Example:

      GOLDEN_UPDATE=1 uv run pytest tests/test_golden_import.py

KNOWN-BUG NOTE: case ``book62`` currently emits orphaned ``[^1]``..``[^5]``
footnote references with NO matching definitions (the converter drops the
footnote bodies). Freezing that buggy output now is intentional — Phase 4 fixes
the bug and updates this golden on purpose, with the diff under review.

Determinism: tests do not depend on machine-local paths and guard on
pandoc + PIL availability (both installed here, so they RUN). The DOCX artifact
and image BYTES are deliberately NOT asserted for equality — they pass through
`docx_optimize`/PIL and may not be byte-deterministic; only the markdown body,
the (allowlisted) frontmatter, the sorted image FILENAME set, and the
bibliography are treated as the deterministic contract.
"""

from __future__ import annotations

import difflib
import importlib.util
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Mapping


ROOT = Path(__file__).resolve().parents[2]

from pancratius import import_docx  # noqa: E402
from pancratius import footnotes  # noqa: E402
from pancratius.content_catalog import KIND_DIRS, dump_frontmatter, split_frontmatter  # noqa: E402


pytestmark = pytest.mark.skipif(
    shutil.which("pandoc") is None or importlib.util.find_spec("PIL") is None,
    reason="pandoc and pillow are required",
)

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
GOLDEN_UPDATE = os.environ.get("GOLDEN_UPDATE") == "1"

# Frontmatter keys that are part of the structural contract we freeze. Anything
# not in this set is treated as volatile/CLI-incidental and dropped from the
# snapshot. There are currently no time/host/run-dependent keys in the output;
# the allowlist makes the exclusion explicit rather than relying on a blocklist.
STRUCTURAL_FM_KEYS = (
    "kind",
    "number",
    "slug",
    "title",
    "lang",
    "tags",
    "cover",
    "cover_is_placeholder",
    "translation",
    "cross_refs",
    "date",
    "description",
)


@dataclass(frozen=True)
class GoldenCase:
    """One frozen import scenario.

    ``signals`` is documentation only — it records which behavior(s) the case is
    responsible for covering so the net's coverage is self-describing.
    """

    name: str
    docx: str
    kind: str
    lang: str
    number: int
    slug: str
    title: str
    signals: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Chosen cases (smallest real source DOCX that covers each required signal):
#
#   poem01 — verse / stanzas.
#            legacy/poetry/01. А если буду я не прав/...docx
#   book62 — body images + bibliography table + the KNOWN orphaned-[^N]
#            footnote bug (5 refs, 0 defs). One case, three signals.
#            legacy/books/ru/62-книга-тишины.docx
#   book23 — well-formed footnotes (1 ref + 1 def). Stored as a regular IR
#            snapshot. (It used to anchor a committed-corpus cross-check: its
#            imported body had to equal the committed
#            src/content/books/23-lichnost-i-ego/ru.md body byte-for-byte. That
#            held under the GFM engine that produced the committed corpus; after
#            the 6.2 cutover the IR importer diverges from the legacy committed
#            file BY DESIGN, so the cross-check is gone and book23 is a stored IR
#            golden like the others.)
#            legacy/books/ru/23-личность-и-эго.docx
#   book18 — ordinary prose / Q&A (no footnotes, images, or bibliography).
#            legacy/books/ru/18-евангелие-от-иисуса.docx
# ---------------------------------------------------------------------------
CASES: tuple[GoldenCase, ...] = (
    GoldenCase(
        name="poem01",
        docx="legacy/poetry/01. А если буду я не прав/А если буду я не прав.docx",
        kind="poem",
        lang="ru",
        number=1,
        slug="a-esli-budu-ya-ne-prav",
        title="А если буду я не прав",
        signals=("verse/stanzas",),
    ),
    GoldenCase(
        name="book62",
        docx="legacy/books/ru/62-книга-тишины.docx",
        kind="book",
        lang="ru",
        number=62,
        slug="kniga-tishiny",
        title="Книга Тишины",
        signals=("body-images", "bibliography-table", "orphaned-footnote-bug"),
    ),
    GoldenCase(
        name="book23",
        docx="legacy/books/ru/23-личность-и-эго.docx",
        kind="book",
        lang="ru",
        number=23,
        slug="lichnost-i-ego",
        title="Личность и эго",
        signals=("footnotes",),
    ),
    GoldenCase(
        name="book18",
        docx="legacy/books/ru/18-евангелие-от-иисуса.docx",
        kind="book",
        lang="ru",
        number=18,
        slug="evangelie-ot-iisusa",
        title="Евангелие от Иисуса",
        signals=("prose/Q&A",),
    ),
)

# All cases get a stored body/frontmatter/assets/biblio snapshot under
# tests/golden/<case>/.
#
# HISTORICAL NOTE (6.2 cutover): book23 used to be EXCLUDED from stored snapshots
# and frozen instead by `test_pure_converter_cross_check`, which asserted its
# imported body was byte-identical to the committed corpus file
# `src/content/books/23-lichnost-i-ego/ru.md`. That committed file is GFM-era
# converter output (the live engine before the cutover); the IR importer is now
# the truth and diverges from it BY DESIGN, so that cross-check no longer holds
# and was removed. Re-importing any committed book through the live importer now
# produces the new IR shape — that is the expected new behavior, NOT a regression,
# and must NOT be "fixed" by re-importing the corpus (that would clobber any
# hand-edits in the committed files). book23 is now a regular stored IR snapshot.
SNAPSHOT_CASES: tuple[GoldenCase, ...] = CASES


@dataclass(frozen=True)
class ImportSnapshot:
    """Deterministic projection of one import run."""

    body: str
    frontmatter: dict[str, object]
    assets: list[str]
    bibliography: str | None


def _structural_frontmatter(fm: Mapping[str, object]) -> dict[str, object]:
    """Reduce a frontmatter dict to its frozen, structural subset."""
    return {key: fm[key] for key in STRUCTURAL_FM_KEYS if key in fm}


def _import_case(case: GoldenCase, content_root: Path) -> ImportSnapshot:
    """Run the live importer for one case into ``content_root`` and project it."""
    report = import_docx.import_work(import_docx.ImportRequest(
        docx=ROOT / case.docx,
        kind=case.kind,
        lang=case.lang,
        number=case.number,
        slug=case.slug,
        title=case.title,
        out_content=content_root,
    ))
    assert not report.refused

    work_dir = content_root / KIND_DIRS[case.kind] / f"{case.number:02d}-{case.slug}"
    md_path = work_dir / f"{case.lang}.md"
    fm, body = split_frontmatter(md_path.read_text(encoding="utf-8"))

    images_dir = work_dir / "images"
    assets = (
        sorted(
            p.relative_to(work_dir).as_posix()
            for p in images_dir.rglob("*")
            if p.is_file()
        )
        if images_dir.is_dir()
        else []
    )

    bib_path = work_dir / "bibliography.yaml"
    bibliography = bib_path.read_text(encoding="utf-8") if bib_path.is_file() else None

    return ImportSnapshot(
        body=body,
        frontmatter=_structural_frontmatter(fm),
        assets=assets,
        bibliography=bibliography,
    )


def _case_golden_dir(case: GoldenCase) -> Path:
    return GOLDEN_DIR / case.name


def _write_golden(case: GoldenCase, snap: ImportSnapshot) -> None:
    out = _case_golden_dir(case)
    out.mkdir(parents=True, exist_ok=True)
    (out / "body.md").write_text(snap.body, encoding="utf-8")
    (out / "frontmatter.yaml").write_text(dump_frontmatter(snap.frontmatter), encoding="utf-8")
    (out / "assets.txt").write_text(
        "".join(f"{name}\n" for name in snap.assets), encoding="utf-8"
    )
    bib_golden = out / "bibliography.yaml"
    if snap.bibliography is not None:
        bib_golden.write_text(snap.bibliography, encoding="utf-8")
    elif bib_golden.exists():
        bib_golden.unlink()


def _text_diff(label: str, expected: str, actual: str) -> str:
    diff = difflib.unified_diff(
        expected.splitlines(keepends=True),
        actual.splitlines(keepends=True),
        fromfile=f"golden/{label}",
        tofile=f"imported/{label}",
    )
    return "".join(diff)


def _assert_against_golden(case: GoldenCase, snap: ImportSnapshot) -> None:
    out = _case_golden_dir(case)
    assert out.is_dir(), (
        f"missing golden dir for {case.name}: {out}. "
        "Run `GOLDEN_UPDATE=1 uv run pytest tests/test_golden_import.py` to seed it."
    )

    body_golden = (out / "body.md").read_text(encoding="utf-8")
    assert snap.body == body_golden, (
        f"{case.name}: body markdown drifted from golden:\n"
        + _text_diff("body.md", body_golden, snap.body)
    )

    fm_golden = (out / "frontmatter.yaml").read_text(encoding="utf-8")
    fm_actual = dump_frontmatter(snap.frontmatter)
    assert fm_actual == fm_golden, (
        f"{case.name}: frontmatter drifted from golden:\n"
        + _text_diff("frontmatter.yaml", fm_golden, fm_actual)
    )

    assets_golden = (out / "assets.txt").read_text(encoding="utf-8")
    assets_actual = "".join(f"{name}\n" for name in snap.assets)
    assert assets_actual == assets_golden, (
        f"{case.name}: produced image filenames drifted from golden:\n"
        + _text_diff("assets.txt", assets_golden, assets_actual)
    )

    bib_golden_path = out / "bibliography.yaml"
    if snap.bibliography is None:
        assert not bib_golden_path.exists(), (
            f"{case.name}: golden has a bibliography.yaml but the import produced none."
        )
    else:
        assert bib_golden_path.exists(), (
            f"{case.name}: import produced a bibliography.yaml but no golden exists."
        )
        bib_golden = bib_golden_path.read_text(encoding="utf-8")
        assert snap.bibliography == bib_golden, (
            f"{case.name}: bibliography.yaml drifted from golden:\n"
            + _text_diff("bibliography.yaml", bib_golden, snap.bibliography)
        )


@pytest.mark.parametrize("case", SNAPSHOT_CASES, ids=[c.name for c in SNAPSHOT_CASES])
def test_golden_import(case: GoldenCase, tmp_path: Path) -> None:
    """Freeze (or assert against) the importer output for each snapshot case.

    Post-6.2-cutover the goldens reflect the LIVE typed-IR converter output.
    """
    snap = _import_case(case, tmp_path / "src" / "content")
    if GOLDEN_UPDATE:
        _write_golden(case, snap)
        pytest.skip(f"GOLDEN_UPDATE=1: regenerated golden for {case.name}")
    _assert_against_golden(case, snap)


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_import_is_idempotent(case: GoldenCase, tmp_path: Path) -> None:
    """Two imports of the same source agree on body, frontmatter, assets, biblio.

    The DOCX artifact and image bytes are intentionally excluded — they go
    through docx_optimize/PIL and are not guaranteed byte-deterministic.
    """
    first = _import_case(case, tmp_path / "run1" / "src" / "content")
    second = _import_case(case, tmp_path / "run2" / "src" / "content")

    assert first.body == second.body, (
        f"{case.name}: body markdown is non-deterministic across runs:\n"
        + _text_diff("body.md", first.body, second.body)
    )
    assert first.frontmatter == second.frontmatter, (
        f"{case.name}: frontmatter is non-deterministic across runs"
    )
    assert first.assets == second.assets, (
        f"{case.name}: produced image filename set is non-deterministic across runs:\n"
        f"  run1={first.assets}\n  run2={second.assets}"
    )
    assert first.bibliography == second.bibliography, (
        f"{case.name}: bibliography.yaml is non-deterministic across runs"
    )


# NOTE (6.2 cutover): the former ``test_pure_converter_cross_check`` asserted
# book23's imported body equalled the committed corpus body
# (`src/content/books/23-lichnost-i-ego/ru.md`) byte-for-byte. That committed file
# is GFM-era output; the live importer is now the typed-IR truth and diverges from
# the legacy committed file BY DESIGN, so the cross-check was removed and book23 is
# a stored IR snapshot (see SNAPSHOT_CASES). Idempotency of every case — including
# book23 — is still proven by ``test_import_is_idempotent``.


# Cases whose source carries footnotes — the property below must hold for them
# (every `[^N]` reference has a matching `[^N]:` definition). book62 is the
# regression case (the orphaned-marker bug); book23 has one well-formed footnote.
FOOTNOTE_CASES: tuple[GoldenCase, ...] = tuple(
    c for c in CASES if c.name in {"book62", "book23"}
)


@pytest.mark.parametrize("case", FOOTNOTE_CASES, ids=[c.name for c in FOOTNOTE_CASES])
def test_imported_footnotes_resolve(case: GoldenCase, tmp_path: Path) -> None:
    """Every `[^N]` reference in a footnoted import has a matching `[^N]:`
    definition — no orphans. This is the property the Phase-4 fix establishes and
    the `analyze_footnotes` FATAL guards; here we assert it on real imports."""
    snap = _import_case(case, tmp_path / "src" / "content")
    refs = set(footnotes.reference_ids(snap.body))
    defs = set(footnotes.definition_ids(snap.body))
    assert refs, f"{case.name}: expected footnote references but found none"
    orphans = sorted(refs - defs)
    assert not orphans, (
        f"{case.name}: orphaned footnote references with no definition: "
        + ", ".join(f"[^{o}]" for o in orphans)
    )
    # And the first-class analysis agrees there are zero fatal findings.
    fatal = [d for d in footnotes.analyze_footnotes(snap.body) if d.severity == "fatal"]
    assert not fatal, f"{case.name}: analyze_footnotes reported fatal(s): {fatal}"
