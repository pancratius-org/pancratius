"""Golden test net: freeze the CURRENT DOCX->Markdown importer behavior.

Phase 2a deliverable. The single job of this module is to lock down what the
live import path (`import_docx.run` -> `lib.docx_conversion.convert_single_docx`
-> `docx_to_md` primitives) produces TODAY, so later refactor phases can be
proven behavior-preserving against a committed snapshot.

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
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Mapping


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import import_docx  # noqa: E402
from lib.content_catalog import dump_frontmatter, split_frontmatter  # noqa: E402


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
#   book23 — well-formed footnotes (1 ref + 1 def) AND the pure-converter
#            cross-check (its imported body is byte-identical to the committed
#            src/content/books/23-lichnost-i-ego/ru.md body).
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
        signals=("footnotes", "pure-converter-cross-check"),
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

# The single case proven (in Phase-2a QA) to be unmodified converter output:
# its imported body must equal the committed corpus body byte-for-byte. This case
# is frozen by that cross-check ALONE — we don't also store a body.md snapshot for
# it, which would just duplicate the committed corpus file (~800KB) without adding
# coverage (the cross-check already freezes its full body, footnotes included).
CROSS_CHECK_CASE = "book23"
CROSS_CHECK_COMMITTED = ROOT / "src" / "content" / "books" / "23-lichnost-i-ego" / "ru.md"

# Cases that get a stored body/frontmatter/assets/biblio snapshot under
# tests/golden/<case>/. The cross-check case is excluded (see above).
SNAPSHOT_CASES: tuple[GoldenCase, ...] = tuple(c for c in CASES if c.name != CROSS_CHECK_CASE)


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
    parsed = import_docx.build_parser().parse_args(
        [
            str(ROOT / case.docx),
            "--kind",
            case.kind,
            "--lang",
            case.lang,
            "--number",
            str(case.number),
            "--slug",
            case.slug,
            "--title",
            case.title,
            "--out-content",
            str(content_root),
        ]
    )
    result = import_docx.run(parsed)

    fm, body = split_frontmatter(result.md_path.read_text(encoding="utf-8"))
    work_dir = result.md_path.parent

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

    The cross-check case (book23) is excluded — it is frozen byte-for-byte by
    ``test_pure_converter_cross_check`` against the committed corpus instead.
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


def test_pure_converter_cross_check(tmp_path: Path) -> None:
    """The cross-check case's imported body equals the committed corpus body.

    Proves the golden net reflects the REAL committed corpus, not just a
    self-consistent re-import. The committed bundle for
    `src/content/books/23-lichnost-i-ego/ru.md` is known-unmodified converter
    output; its body must match the freshly imported body byte-for-byte.
    """
    case = next(c for c in CASES if c.name == CROSS_CHECK_CASE)
    snap = _import_case(case, tmp_path / "src" / "content")

    assert CROSS_CHECK_COMMITTED.is_file(), f"missing committed corpus file: {CROSS_CHECK_COMMITTED}"
    _fm, committed_body = split_frontmatter(
        CROSS_CHECK_COMMITTED.read_text(encoding="utf-8")
    )

    assert snap.body == committed_body, (
        f"{case.name}: imported body diverged from the committed corpus body "
        f"({CROSS_CHECK_COMMITTED}):\n"
        + _text_diff("ru.md body", committed_body, snap.body)
    )
