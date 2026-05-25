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
