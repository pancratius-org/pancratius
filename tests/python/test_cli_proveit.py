"""TDD verification of the `pancratius` library door — prove-it / locking tests.

These complement `tests/test_cli.py` (which proves the dispatch contract in
isolation via monkeypatch). Here we close the gaps a TDD reviewer flags as
*shallow* or *missing*, and lock behaviour the build CLAIMS but does not yet pin:

  - `work import` maps every argparse flag onto `ImportRequest`, not just the
    four fields the existing test spot-checks.
  - `--replace` semantics through the genuine writer (refuse without, overwrite
    with) — completely untested before.
  - re-import idempotency (import twice → identical bundle; second run reports
    skips, not changes) — the writer's no-clobber/byte-equal contract.
  - the writer-relayer split: `import_work` writes the provenance manifest;
    `scaffold_subpage` writes NONE; a dry-run / refusal writes none either.
  - the extras gate REPRODUCED for real (the heavy stacks are not installed in
    this env), not just simulated by injecting a broken module.
  - the lib-ification preservation: `generate_graph(only=None)` actually runs
    BOTH projections (the documented contract), beyond the door passing only=None.
  - the scaffold draft is provably schema-INVALID against the real Zod enum,
    not merely "weight startswith TODO".

CONSTRAINTS honoured: source files are NOT modified; every mutating test runs
against a scratch tmp `--out-content`, never the real corpus.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType

import pytest

from pancratius import cli
from pancratius.pandoc import PandocExecutable

ROOT = Path(__file__).resolve().parents[2]

_FIXTURE_DOCX = ROOT / "legacy" / "books" / "ru" / "23-личность-и-эго.docx"
_REQUIRES_REAL = pytest.mark.skipif(
    shutil.which("pandoc") is None
    or importlib.util.find_spec("PIL") is None
    or not _FIXTURE_DOCX.is_file(),
    reason="pandoc, pillow, and the fixture DOCX are required",
)


def _exit_code(argv: list[str]) -> int:
    try:
        return cli.main(argv)
    except SystemExit as exc:
        return int(exc.code or 0)


def _fake_pandoc() -> PandocExecutable:
    return PandocExecutable("/usr/bin/pandoc", "path")


# ===========================================================================
# 1) work import — FULL 1:1 flag→ImportRequest map
#    The existing suite only asserts kind/lang/dry_run/docx; the door documents
#    every CLI flag must cross into the typed importer request. Lock every mapping
#    through the real door, so a dropped flag-to-domain wire is caught.
# ===========================================================================
def test_work_import_maps_every_flag_onto_request(monkeypatch: pytest.MonkeyPatch) -> None:
    from pancratius import import_docx

    monkeypatch.setattr(cli, "find_pandoc", lambda: _fake_pandoc())
    captured: list[object] = []
    monkeypatch.setattr(
        import_docx,
        "import_work",
        lambda request: (captured.append(request), import_docx.WriteReport((), (), (), (), ()))[1],
    )

    rc = _exit_code(
        [
            "work", "import", "src.docx",
            "--to", "poem:42",
            "--lang", "en",
            "--title", "My Title",
            "--slug", "custom-slug",
            "--description", "A description",
            "--cover", "cover.png",
            "--translation-source", "literary",
            "--out-content", "/tmp/scratch/content",
            "--dry-run",
            "--replace",
        ]
    )
    assert rc == 0
    (req,) = captured
    assert isinstance(req, import_docx.ImportRequest)
    assert req.docx == Path("src.docx")
    assert req.lang == "en"
    assert req.out_content == Path("/tmp/scratch/content")
    assert isinstance(req.target, import_docx.ExplicitWorkTarget)
    assert req.target.kind == "poem"
    assert req.target.number == 42
    assert req.target.slug == "custom-slug"
    assert req.text.title == "My Title"
    assert req.text.description == "A description"
    assert isinstance(req.cover, import_docx.CoverFile)
    assert req.cover.path == Path("cover.png")
    assert isinstance(req.translation, import_docx.TranslationSourceOverride)
    assert req.translation.source == "literary"
    assert req.write.dry_run is True
    assert req.write.replace is True


def test_work_import_replace_flag_defaults_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Absent --replace, the request's replace must be False (re-import is additive)."""
    from pancratius import import_docx

    monkeypatch.setattr(cli, "find_pandoc", lambda: _fake_pandoc())
    captured: list[import_docx.ImportRequest] = []
    monkeypatch.setattr(
        import_docx,
        "import_work",
        lambda request: (captured.append(request), import_docx.WriteReport((), (), (), (), ()))[1],
    )
    assert _exit_code(["work", "import", "x.docx", "--kind", "book", "--lang", "ru"]) == 0
    (req,) = captured
    assert req.write.replace is False
    assert req.write.dry_run is False


# ===========================================================================
# 2) --replace semantics through the GENUINE writer (real conversion, scratch root)
# ===========================================================================
def _import_new(content_root: Path, *extra: str) -> int:
    """First import of a NEW explicitly numbered work bundle."""
    return cli.main(
        [
            "work", "import", str(_FIXTURE_DOCX),
            "--to", "book:91", "--lang", "ru",
            "--slug", "replace-probe",
            "--out-content", str(content_root),
            *extra,
        ]
    )


def _reimport_to(content_root: Path, *extra: str) -> int:
    """Re-import the SAME language to the existing selector — the path that
    reaches the writer's overwrite-refused / --replace boundary."""
    return cli.main(
        [
            "work", "import", str(_FIXTURE_DOCX),
            "--to", "book:91", "--lang", "ru",
            "--out-content", str(content_root),
            *extra,
        ]
    )


@_REQUIRES_REAL
def test_work_import_to_existing_selector_new_lang_needs_no_replace(
    tmp_path: Path,
) -> None:
    """A selector can add a missing locale to an existing work without --replace;
    the writer only requires --replace when a concrete file would be overwritten."""
    content_root = tmp_path / "src" / "content"
    assert _import_new(content_root) == 0
    rc = cli.main(
        [
            "work", "import", str(_FIXTURE_DOCX),
            "--to", "book:91", "--lang", "en",
            "--out-content", str(content_root),
        ]
    )
    assert rc == 0
    assert (content_root / "books" / "91-replace-probe" / "en.md").is_file()


@_REQUIRES_REAL
def test_work_import_reimport_same_lang_refused_without_replace(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Re-importing the SAME language via --to without --replace must be refused
    by the writer, never silently clobbering the converter-owned <lang>.md."""
    content_root = tmp_path / "src" / "content"
    assert _import_new(content_root) == 0, "first import should succeed"
    md = content_root / "books" / "91-replace-probe" / "ru.md"
    assert md.is_file()
    first_bytes = md.read_bytes()
    capsys.readouterr()  # drain

    rc = _reimport_to(content_root)  # --to, no --replace
    out = capsys.readouterr()
    assert rc == 1, "re-import without --replace must be a writer refusal"
    assert "overwrite-refused" in out.out + out.err
    assert md.read_bytes() == first_bytes, "refused re-import must not touch the file"


@_REQUIRES_REAL
def test_work_import_replace_permits_overwrite(tmp_path: Path) -> None:
    """With --replace, re-importing the same language via --to is permitted: no
    overwrite-refused fatal, and a locally-edited <lang>.md is rewritten to the
    freshly-converted bytes."""
    content_root = tmp_path / "src" / "content"
    assert _import_new(content_root) == 0
    md = content_root / "books" / "91-replace-probe" / "ru.md"
    # Mutate the committed file so a real overwrite is observable, then --replace.
    md.write_text(md.read_text(encoding="utf-8") + "\n<!-- locally edited -->\n", encoding="utf-8")
    rc = _reimport_to(content_root, "--replace")
    assert rc == 0, "--replace must permit overwriting the existing <lang>.md"
    assert "<!-- locally edited -->" not in md.read_text(encoding="utf-8"), (
        "--replace must have rewritten the file to the freshly-converted bytes"
    )


# ===========================================================================
# 3) Re-import idempotency — import twice → identical bundle (writer byte-equal)
# ===========================================================================
@_REQUIRES_REAL
def test_work_import_is_idempotent_second_run_skips(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Importing the same DOCX twice yields a byte-identical bundle. The writer
    classifies unchanged bytes as `skip`, so a --replace re-run of an unchanged
    source reports skips (not changes) and the file bytes are stable."""
    content_root = tmp_path / "src" / "content"
    assert _import_new(content_root) == 0
    bundle = content_root / "books" / "91-replace-probe"
    snapshot = {p.relative_to(content_root).as_posix(): p.read_bytes()
                for p in sorted(bundle.rglob("*")) if p.is_file()}
    capsys.readouterr()

    rc = _reimport_to(content_root, "--replace")
    out = capsys.readouterr()
    assert rc == 0
    after = {p.relative_to(content_root).as_posix(): p.read_bytes()
             for p in sorted(bundle.rglob("*")) if p.is_file()}
    assert after == snapshot, "re-import must produce a byte-identical bundle"
    # The canonical <lang>.md re-run with identical bytes is a skip, not a change.
    assert "skip: books/91-replace-probe/ru.md" in out.out
    assert "change: books/91-replace-probe/ru.md" not in out.out


# ===========================================================================
# 4) Writer relayer — import_work writes the manifest; scaffold_subpage none;
#    dry-run / refusal write none. Manifest lands under <root>/../../data/imports
#    (sandboxed away from the real repo by the scratch --out-content).
# ===========================================================================
def _imports_dir_for(content_root: Path) -> Path:
    # Mirrors pancratius.paths.imports_dir_for_content_root for a scratch content root.
    return content_root.parents[1] / "data" / "imports"


@_REQUIRES_REAL
def test_import_work_writes_provenance_manifest(tmp_path: Path) -> None:
    """A real (non-dry-run, non-refused) import writes ONE provenance manifest
    under data/imports/, named from the full scope, recording the source sha256."""
    import json

    content_root = tmp_path / "src" / "content"
    assert _import_new(content_root) == 0
    manifest = _imports_dir_for(content_root) / "books-91-replace-probe.json"
    assert manifest.is_file(), "import_work must relay the provenance manifest"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["target_scope"] == "books/91-replace-probe"
    assert data["source_document"].endswith("23-личность-и-эго.docx")
    assert isinstance(data["source_sha256"], str) and len(data["source_sha256"]) == 64
    assert data["operations"], "the manifest must record the op list"


@_REQUIRES_REAL
def test_import_work_dry_run_writes_no_manifest(tmp_path: Path) -> None:
    """--dry-run must not write the provenance manifest (provenance is only for a
    real apply that actually wrote)."""
    content_root = tmp_path / "src" / "content"
    rc = _import_new(content_root, "--dry-run")
    assert rc == 0
    imports = _imports_dir_for(content_root)
    assert not imports.exists() or list(imports.glob("*.json")) == [], (
        "dry-run must not relay a manifest"
    )


@_REQUIRES_REAL
def test_scaffold_subpage_writes_no_manifest(tmp_path: Path) -> None:
    """`project page add` (scaffold_subpage) reuses the GENERAL writer and is NOT
    an import: it must NEVER write a provenance manifest (the relayer split)."""
    content_root = tmp_path / "src" / "content"
    rc = cli.main(
        [
            "project", "page", "add", "project:holy-rus/manifest-probe", str(_FIXTURE_DOCX),
            "--lang", "ru",
            "--out-content", str(content_root),
        ]
    )
    assert rc == 0
    assert (content_root / "projects" / "holy-rus" / "subpages" / "manifest-probe" / "ru.md").is_file()
    imports = _imports_dir_for(content_root)
    assert not imports.exists() or list(imports.glob("*.json")) == [], (
        "scaffold_subpage must write NO provenance manifest"
    )


# ===========================================================================
# 5) Extras gate — REPRODUCED for real (heavy stacks are not installed here)
#    No mocks: invoke the genuine door; the owner import fails on its own.
# ===========================================================================
_GRAPH_INSTALLED = importlib.util.find_spec("igraph") is not None
_EMBED_INSTALLED = importlib.util.find_spec("numpy") is not None


@pytest.mark.skipif(_GRAPH_INSTALLED, reason="graph extra installed; cannot reproduce the gate")
def test_conceptosphere_graph_generate_extras_gate_reproduced(capsys: pytest.CaptureFixture[str]) -> None:
    """With the `graph` extra genuinely absent, `conceptosphere graph generate` exits 1 with
    the `uv sync --extra graph` hint and prints NO traceback (no mocks)."""
    rc = _exit_code(["conceptosphere", "graph", "generate"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "uv sync --extra graph" in err
    assert "Traceback (most recent call last)" not in err


@pytest.mark.skipif(_EMBED_INSTALLED, reason="embed extra installed; cannot reproduce the gate")
def test_conceptosphere_embed_generate_extras_gate_reproduced(capsys: pytest.CaptureFixture[str]) -> None:
    """With the `embed` extra genuinely absent, `conceptosphere embed generate` exits 1 with
    the `uv sync --extra embed` hint and prints NO traceback (no mocks)."""
    rc = _exit_code(["conceptosphere", "embed", "generate"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "uv sync --extra embed" in err
    assert "Traceback (most recent call last)" not in err


# ===========================================================================
# 6) lib-ification preservation — generate_graph(only=None) runs BOTH projections.
#    The existing test only proves the door passes only=None; this locks the
#    DOCUMENTED behaviour of the lib function itself (the migration's real claim).
#    process_corpus + run_*_mode are stubbed so no corpus/heavy work is needed.
# ===========================================================================
_HEAVY_CS_DEPS = ("igraph", "leidenalg", "networkx", "pymorphy3", "regex", "community", "numpy")


@pytest.fixture
def conceptosphere_module() -> Iterator[ModuleType]:
    """Import pancratius.conceptosphere with its heavy deps faked by MagicMock, so
    the module body (which calls e.g. `regex.compile(...)` at import time) loads and
    `generate_graph` can be exercised — the extra is genuinely absent in this env.

    MagicMock absorbs arbitrary attribute access (`re2.compile`, `re2.UNICODE`, …),
    so we never hand-build each heavy API. TEARS DOWN cleanly: it removes the stubs
    and the cached conceptosphere on exit, so the light-core no-ML invariant other
    tests assert is never polluted by this fixture."""
    import importlib as _il
    from unittest.mock import MagicMock

    injected: list[str] = []
    for name in _HEAVY_CS_DEPS:
        if name in sys.modules:
            continue
        try:
            if importlib.util.find_spec(name) is not None:
                continue  # real dep present — leave it
        except (ValueError, ModuleNotFoundError):
            pass
        sys.modules[name] = MagicMock(name=name)
        injected.append(name)

    sys.modules.pop("pancratius.conceptosphere", None)
    try:
        yield _il.import_module("pancratius.conceptosphere")
    finally:
        for name in injected:
            sys.modules.pop(name, None)
        sys.modules.pop("pancratius.conceptosphere", None)


def _fake_corpus_bundle(_log: object) -> object:
    return object()


def _record_projection(ran: list[str], label: str) -> Callable[..., None]:
    def record(*_args: object, **_kwargs: object) -> None:
        ran.append(label)

    return record


def test_generate_graph_none_runs_both_projections(
    conceptosphere_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """generate_graph(only=None) must invoke BOTH run_concepts_mode AND
    run_books_mode off a single corpus scan — the migration's behaviour contract."""
    cs = conceptosphere_module
    ran: list[str] = []
    monkeypatch.setattr(cs, "process_corpus", _fake_corpus_bundle)
    monkeypatch.setattr(cs, "run_concepts_mode", _record_projection(ran, "concepts"))
    monkeypatch.setattr(cs, "run_books_mode", _record_projection(ran, "books"))

    cs.generate_graph(only=None)
    assert ran == ["concepts", "books"], "only=None must run both projections, once each"


@pytest.mark.parametrize(("only", "expected"), [("concepts", ["concepts"]), ("books", ["books"])])
def test_generate_graph_only_runs_single_projection(
    conceptosphere_module: ModuleType, monkeypatch: pytest.MonkeyPatch, only: str, expected: list[str]
) -> None:
    """generate_graph(only=X) must run ONLY projection X (single-mode preserved)."""
    cs = conceptosphere_module
    ran: list[str] = []
    monkeypatch.setattr(cs, "process_corpus", _fake_corpus_bundle)
    monkeypatch.setattr(cs, "run_concepts_mode", _record_projection(ran, "concepts"))
    monkeypatch.setattr(cs, "run_books_mode", _record_projection(ran, "books"))
    cs.generate_graph(only=only)
    assert ran == expected


def test_generate_graph_projection_error_propagates_after_both_runs(
    conceptosphere_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed concepts projection must NOT skip books, and the typed domain
    error must propagate after both projections have had a chance to run."""
    cs = conceptosphere_module
    ran: list[str] = []

    def fail_concepts(*_args: object, **_kwargs: object) -> None:
        ran.append("concepts")
        raise cs.GraphGenerationError("synthetic concepts failure")

    monkeypatch.setattr(cs, "process_corpus", _fake_corpus_bundle)
    monkeypatch.setattr(cs, "run_concepts_mode", fail_concepts)
    monkeypatch.setattr(cs, "run_books_mode", _record_projection(ran, "books"))
    with pytest.raises(cs.GraphGenerationError):
        cs.generate_graph(only=None)
    assert ran == ["concepts", "books"], "books must still run after a concepts failure"


# ===========================================================================
# 7) Scaffold draft is provably schema-INVALID against the REAL Zod enum
#    (stronger than 'weight startswith TODO'): the seeded weight is genuinely
#    outside src/content.config.ts's projectSubpageWeight enum.
# ===========================================================================
@_REQUIRES_REAL
def test_scaffolded_weight_is_outside_the_real_zod_enum(tmp_path: Path) -> None:
    """The draft's TODO weight must be a value the real Zod enum rejects, so the
    draft genuinely fails `npm run check` (the safe-incomplete contract) — not
    merely a string that happens to start with TODO."""
    from pancratius.content_catalog import split_frontmatter

    # Parse the real enum members out of src/content.config.ts.
    config = (ROOT / "src" / "content.config.ts").read_text(encoding="utf-8")
    block = re.search(r"projectSubpageWeight\s*=\s*z\.enum\(\[(.*?)\]\)", config, re.S)
    assert block, "could not locate projectSubpageWeight enum in content.config.ts"
    valid = set(re.findall(r'"([^"]+)"', block.group(1)))
    assert valid == {"essay", "revelation", "verse", "practice", "dialogue"}

    content_root = tmp_path / "src" / "content"
    rc = cli.main(
        [
            "project", "page", "add", "project:holy-rus/schema-probe", str(_FIXTURE_DOCX),
            "--lang", "ru",
            "--out-content", str(content_root),
        ]
    )
    assert rc == 0
    subpage = content_root / "projects" / "holy-rus" / "subpages" / "schema-probe" / "ru.md"
    fm, _ = split_frontmatter(subpage.read_text(encoding="utf-8"))
    assert fm["weight"] not in valid, "the seeded weight MUST fail the real enum"
    # title/description are also placeholders (the editorial register stays unset).
    assert str(fm["title"]).startswith("TODO")
    assert str(fm["description"]).startswith("TODO")
