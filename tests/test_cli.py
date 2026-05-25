"""Behavioural tests for the `pancratius` library door (docs/tooling.md).

These assert the *dispatch contract*: each verb routes to its owner entry, and the
door's uniform exit codes hold (0 ok / 1 refusal-or-failure / 2 usage). Owners are
monkeypatched so the door is tested in isolation — never against the real corpus.
The owners' own behaviour is covered by their dedicated tests.
"""

from __future__ import annotations

from pathlib import Path
import importlib.util
import shutil
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from pancratius import cli  # noqa: E402


def _exit_code(argv: list[str]) -> int:
    """Run the door, normalising argparse's SystemExit(code) and a returned int to a
    single comparable exit code (argparse usage errors raise SystemExit(2))."""
    try:
        return cli.main(argv)
    except SystemExit as exc:  # argparse --help / usage error
        return int(exc.code or 0)


# --- the navigable ontology (--help at every level exits 0) -------------------
@pytest.mark.parametrize(
    "argv",
    [["--help"], ["data", "--help"], ["data", "slug-map", "--help"], ["data", "bulk", "--help"]],
)
def test_help_exits_zero(argv: list[str]) -> None:
    assert _exit_code(argv) == 0


# --- usage errors are exit 2 --------------------------------------------------
@pytest.mark.parametrize("argv", [[], ["data"], ["data", "slug-map"], ["data", "bulk"]])
def test_bare_group_or_noun_is_usage_error(argv: list[str]) -> None:
    assert _exit_code(argv) == 2


@pytest.mark.parametrize("argv", [["bogus"], ["data", "bogus"], ["data", "slug-map", "bogus"]])
def test_unknown_command_is_usage_error(argv: list[str]) -> None:
    assert _exit_code(argv) == 2


# --- dispatch + exit-code remap ----------------------------------------------
def test_data_slug_map_refresh_dispatches_to_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    import build_slug_map

    monkeypatch.setattr(build_slug_map, "generate_slug_map", lambda: 0)
    assert _exit_code(["data", "slug-map", "refresh"]) == 0


def test_owner_nonzero_collapses_to_one_not_two(monkeypatch: pytest.MonkeyPatch) -> None:
    """An owner's own nonzero return must surface as 1 (failure), never 2 — exit 2 is
    reserved for argparse usage so callers can distinguish a bad command from a
    failed one. build_slug_map returns 2 on dangling cross_refs; the door maps it."""
    import build_slug_map

    monkeypatch.setattr(build_slug_map, "generate_slug_map", lambda: 2)
    assert _exit_code(["data", "slug-map", "refresh"]) == 1


def test_data_bulk_refresh_shells_to_node(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], *args: object, **kwargs: object) -> types.SimpleNamespace:
        calls.append(cmd)
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    assert _exit_code(["data", "bulk", "refresh"]) == 0
    assert calls and calls[0][0] == "node"
    assert calls[0][-1].endswith("build_bulk_archives.ts")


def test_data_bulk_refresh_node_failure_is_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli.subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=3)
    )
    assert _exit_code(["data", "bulk", "refresh"]) == 1


# --- heavy verbs behind the extras gate --------------------------------------
def test_light_core_imports_no_ml_deps() -> None:
    """Importing the door must not pull a heavy stack — the light core stays light.
    `import pancratius.cli` ran at module load; assert no heavy module rode in."""
    for heavy in ("conceptosphere", "conceptosphere_embed", "networkx", "igraph", "mlx", "numpy"):
        assert heavy not in sys.modules, f"light core unexpectedly imported {heavy}"


@pytest.mark.parametrize(
    ("argv", "owner", "extra"),
    [
        (["data", "graph", "generate"], "conceptosphere", "graph"),
        (["data", "embed", "generate"], "conceptosphere_embed", "embed"),
    ],
)
def test_heavy_verb_without_extra_prints_hint(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    owner: str,
    extra: str,
) -> None:
    """A heavy verb with its extra absent exits 1 with the install hint — never a
    traceback. Forced deterministically by blocking the owner import (so the test
    holds whether or not the extra happens to be installed)."""
    monkeypatch.setitem(sys.modules, owner, None)  # `from <owner> import …` → ImportError
    assert _exit_code(argv) == 1
    err = capsys.readouterr().err
    assert f"uv sync --extra {extra}" in err


def _stub_owner(monkeypatch: pytest.MonkeyPatch, module: str, attr: str) -> list[dict[str, object]]:
    """Inject a fake owner module exposing `attr` as a call-recording stub, so the
    door's `from <module> import <attr>` resolves to it. Returns the calls list."""
    calls: list[dict[str, object]] = []

    def stub(**kwargs: object) -> int:
        calls.append(kwargs)
        return 0

    fake = types.ModuleType(module)
    setattr(fake, attr, stub)
    monkeypatch.setitem(sys.modules, module, fake)
    return calls


def test_data_graph_generate_dispatches_with_only(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_owner(monkeypatch, "conceptosphere", "generate_graph")
    assert _exit_code(["data", "graph", "generate", "--only", "books"]) == 0
    assert calls == [{"only": "books"}]


def test_data_graph_generate_defaults_to_both(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_owner(monkeypatch, "conceptosphere", "generate_graph")
    assert _exit_code(["data", "graph", "generate"]) == 0
    assert calls == [{"only": None}]  # only=None → both projections


def test_data_embed_generate_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_owner(monkeypatch, "conceptosphere_embed", "generate_embeddings")
    assert _exit_code(["data", "embed", "generate"]) == 0
    assert calls == [{}]


# --- work import (writer-backed; door owns the report output) -----------------
def _fake_report(*, refused: tuple[object, ...] = ()) -> types.SimpleNamespace:
    """A stand-in for lib.writer.WriteReport carrying the fields print_report reads."""
    return types.SimpleNamespace(
        created=(), changed=(), skipped=(), refused=refused, diagnostics=()
    )


def test_work_import_builds_request_and_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    """The door maps flags 1:1 onto ImportRequest and calls import_work; a clean
    report exits 0."""
    import import_docx

    monkeypatch.setattr(cli.shutil, "which", lambda _tool: "/usr/bin/pandoc")
    captured: list[object] = []

    def fake_import_work(request: object) -> object:
        captured.append(request)
        return _fake_report()

    monkeypatch.setattr(import_docx, "import_work", fake_import_work)
    rc = _exit_code(
        ["work", "import", "x.docx", "--kind", "book", "--lang", "ru", "--dry-run"]
    )
    assert rc == 0
    (req,) = captured
    assert isinstance(req, import_docx.ImportRequest)
    assert req.kind == "book" and req.lang == "ru" and req.dry_run is True
    assert req.docx == Path("x.docx")


def test_work_import_refusal_is_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import import_docx

    monkeypatch.setattr(cli.shutil, "which", lambda _tool: "/usr/bin/pandoc")
    monkeypatch.setattr(
        import_docx, "import_work", lambda _r: _fake_report(refused=("books/01-x/ru.md",))
    )
    assert _exit_code(["work", "import", "x.docx", "--kind", "book", "--lang", "ru"]) == 1


def test_work_import_input_error_is_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    """import_docx.ImportError (bad input / unresolvable target) is a usage error
    (exit 2), matching the standalone CLI — not the same as the builtin ImportError."""
    import import_docx

    monkeypatch.setattr(cli.shutil, "which", lambda _tool: "/usr/bin/pandoc")

    def boom(_r: object) -> object:
        raise import_docx.ImportError("--kind is required when importing a new work")

    monkeypatch.setattr(import_docx, "import_work", boom)
    assert _exit_code(["work", "import", "x.docx", "--lang", "ru"]) == 2


def test_work_import_missing_pandoc_is_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.shutil, "which", lambda _tool: None)
    assert _exit_code(["work", "import", "x.docx", "--kind", "book", "--lang", "ru"]) == 1


# --- project page add (writer-backed scaffold; door prints the landing entry) -
def _fake_scaffold_report(*, refused: tuple[object, ...] = ()) -> types.SimpleNamespace:
    """A stand-in for the writer's WriteReport carrying the fields print_report reads."""
    return types.SimpleNamespace(
        created=(), changed=(), skipped=(), refused=refused, diagnostics=()
    )


def test_project_page_add_dispatches_and_prints_landing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`project page add` maps its args onto scaffold_subpage and, on a clean report,
    exits 0 and prints the suggested landing `subpages:` entry (with the slug) to stdout."""
    from lib import docx_conversion

    monkeypatch.setattr(cli.shutil, "which", lambda _tool: "/usr/bin/pandoc")
    captured: list[dict[str, object]] = []

    def fake_scaffold(**kwargs: object) -> object:
        captured.append(kwargs)
        return _fake_scaffold_report()

    monkeypatch.setattr(docx_conversion, "scaffold_subpage", fake_scaffold)
    rc = _exit_code(
        ["project", "page", "add", "holy-rus", "my-sub", "doc.docx", "--lang", "ru"]
    )
    assert rc == 0
    (kw,) = captured
    assert kw["project"] == "holy-rus"
    assert kw["subpage_slug"] == "my-sub"
    assert kw["docx"] == Path("doc.docx")
    assert kw["lang"] == "ru"
    assert kw["dry_run"] is False
    out = capsys.readouterr().out
    assert "subpages:" in out
    assert "my-sub" in out, "the landing entry must name the sub-page slug"


def test_project_page_add_refusal_is_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from lib import docx_conversion

    monkeypatch.setattr(cli.shutil, "which", lambda _tool: "/usr/bin/pandoc")
    monkeypatch.setattr(
        docx_conversion,
        "scaffold_subpage",
        lambda **_k: _fake_scaffold_report(refused=("projects/holy-rus/subpages/my-sub/ru.md",)),
    )
    assert _exit_code(["project", "page", "add", "holy-rus", "my-sub", "x.docx", "--lang", "ru"]) == 1


def test_project_page_add_scaffold_error_is_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ScaffoldError (bad input — missing/non-DOCX) is a usage error (exit 2)."""
    from lib import docx_conversion

    monkeypatch.setattr(cli.shutil, "which", lambda _tool: "/usr/bin/pandoc")

    def boom(**_k: object) -> object:
        raise docx_conversion.ScaffoldError("expected a .docx file")

    monkeypatch.setattr(docx_conversion, "scaffold_subpage", boom)
    assert _exit_code(["project", "page", "add", "holy-rus", "my-sub", "x.txt", "--lang", "ru"]) == 2


def test_project_page_add_missing_pandoc_is_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.shutil, "which", lambda _tool: None)
    assert _exit_code(["project", "page", "add", "holy-rus", "my-sub", "x.docx", "--lang", "ru"]) == 1


# --- downloads / docx (pass-through to owner) ---------------------------------
def test_downloads_render_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    import render_downloads

    seen: list[dict[str, object]] = []

    def fake_render(**kwargs: object) -> int:
        seen.append(kwargs)
        return 0

    monkeypatch.setattr(render_downloads, "render", fake_render)
    assert _exit_code(["downloads", "render", "--book", "3"]) == 0
    assert seen and seen[0]["book"] == 3


def test_docx_optimize_dispatches_with_path_objects(monkeypatch: pytest.MonkeyPatch) -> None:
    import docx_optimize

    seen: list[dict[str, object]] = []

    def fake_optimize(**kwargs: object) -> int:
        seen.append(kwargs)
        return 0

    monkeypatch.setattr(docx_optimize, "optimize", fake_optimize)
    assert _exit_code(["docx", "optimize", "a.docx", "--dry-run"]) == 0
    assert seen and seen[0]["paths"] == [Path("a.docx")] and seen[0]["dry_run"] is True


# --- end-to-end: the real door → import_work → writer path (no mocks) ----------
_FIXTURE_DOCX = ROOT / "legacy" / "books" / "ru" / "23-личность-и-эго.docx"


@pytest.mark.skipif(
    shutil.which("pandoc") is None
    or importlib.util.find_spec("PIL") is None
    or not _FIXTURE_DOCX.is_file(),
    reason="pandoc, pillow, and the fixture DOCX are required",
)
def test_work_import_dry_run_writes_nothing(tmp_path: Path) -> None:
    """Drive the genuine path (no monkeypatch): the door builds the request, calls
    import_work, which converts via pandoc, plans, and dry-runs the writer. The
    writer-backed --dry-run must touch nothing — proven against a scratch content
    root, never the real corpus."""
    content_root = tmp_path / "src" / "content"
    rc = cli.main(
        [
            "work", "import", str(_FIXTURE_DOCX),
            "--kind", "book", "--lang", "ru",
            "--number", "91", "--slug", "door-probe",
            "--out-content", str(content_root),
            "--dry-run",
        ]
    )
    assert rc == 0
    written = list(content_root.rglob("*")) if content_root.exists() else []
    assert written == [], f"--dry-run wrote files through the door: {written}"


@pytest.mark.skipif(
    shutil.which("pandoc") is None
    or importlib.util.find_spec("PIL") is None
    or not _FIXTURE_DOCX.is_file(),
    reason="pandoc, pillow, and the fixture DOCX are required",
)
def test_project_page_add_dry_run_writes_nothing(tmp_path: Path) -> None:
    """The genuine door → scaffold_subpage → writer path under --dry-run must touch
    nothing — proven against a scratch content root, never the real corpus."""
    content_root = tmp_path / "src" / "content"
    rc = cli.main(
        [
            "project", "page", "add", "holy-rus", "classification", str(_FIXTURE_DOCX),
            "--lang", "ru",
            "--out-content", str(content_root),
            "--dry-run",
        ]
    )
    assert rc == 0
    written = list(content_root.rglob("*")) if content_root.exists() else []
    assert written == [], f"--dry-run wrote files through the door: {written}"


@pytest.mark.skipif(
    shutil.which("pandoc") is None
    or importlib.util.find_spec("PIL") is None
    or not _FIXTURE_DOCX.is_file(),
    reason="pandoc, pillow, and the fixture DOCX are required",
)
def test_project_page_add_scaffolds_subpage_not_landing(tmp_path: Path) -> None:
    """A real (non-dry-run) scaffold writes the sub-page `<lang>.md` with the
    mechanical frontmatter seeded and the editorial `weight` left TODO, and NEVER
    creates/edits the project landing."""
    from lib.content_catalog import split_frontmatter

    content_root = tmp_path / "src" / "content"
    rc = cli.main(
        [
            "project", "page", "add", "holy-rus", "classification", str(_FIXTURE_DOCX),
            "--lang", "ru",
            "--out-content", str(content_root),
        ]
    )
    assert rc == 0

    subpage = content_root / "projects" / "holy-rus" / "subpages" / "classification" / "ru.md"
    assert subpage.is_file(), "the scaffold must write the sub-page <lang>.md"
    # The landing is never created/edited by the tool.
    assert not (content_root / "projects" / "holy-rus" / "ru.md").exists()

    fm, _body = split_frontmatter(subpage.read_text(encoding="utf-8"))
    assert fm["kind"] == "project_subpage"
    assert fm["parent"] == "holy-rus"
    assert fm["slug"] == "classification"
    assert fm["lang"] == "ru"
    assert str(fm["weight"]).startswith("TODO"), "the editorial register stays a TODO placeholder"


@pytest.mark.skipif(
    shutil.which("pandoc") is None
    or importlib.util.find_spec("PIL") is None
    or not _FIXTURE_DOCX.is_file(),
    reason="pandoc, pillow, and the fixture DOCX are required",
)
@pytest.mark.parametrize(
    ("project", "subpage_slug"),
    [
        ("../../../tmp/evil", "probe"),
        ("/tmp/evil", "probe"),
        ("x/../../etc", "probe"),
        ("holy-rus", "../../../tmp/evil"),
        ("holy-rus", "/etc/passwd"),
        ("holy-rus", "x/../../y"),
    ],
)
def test_project_page_add_scope_escape_is_refused(
    tmp_path: Path, project: str, subpage_slug: str
) -> None:
    """A project OR subpage argument that would escape src/content is refused by the
    writer's path-boundary: every op path embeds the scope, so a `..`/absolute in
    either raw arg trips `writeplan.unsafe-path` (fatal). The scaffold builds the
    scope from raw args, so this boundary is what keeps a hostile slug from writing
    outside the content tree. Pinned on BOTH axes against future refactors."""
    from lib.docx_conversion import scaffold_subpage

    content_root = tmp_path / "src" / "content"
    report = scaffold_subpage(
        project=project, subpage_slug=subpage_slug, docx=_FIXTURE_DOCX, lang="ru",
        out_content=content_root, dry_run=True,
    )
    assert report.refused, "a scope-escaping arg must be refused"
    assert any(d.severity == "fatal" for d in report.diagnostics)
