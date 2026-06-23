"""Behavioural tests for the `pancratius` library door (docs/tooling.md).

These assert the *dispatch contract*: each verb routes to its owner entry, and the
door's uniform exit codes hold (0 ok / 1 refusal-or-failure / 2 usage). Owners are
monkeypatched so the door is tested in isolation — never against the real corpus.
The owners' own behaviour is covered by their dedicated tests.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import types
from pathlib import Path
from typing import Any, cast

import pytest

from pancratius import cli

ROOT = Path(__file__).resolve().parents[2]


def _exit_code(argv: list[str]) -> int:
    """Run the door, normalising argparse's SystemExit(code) and a returned int to a
    single comparable exit code (argparse usage errors raise SystemExit(2))."""
    try:
        return cli.main(argv)
    except SystemExit as exc:  # argparse --help / usage error
        return int(exc.code or 0)


def _catalog_entry(*, kind: str = "book", number: int = 1, lang: str = "ru") -> object:
    from pancratius.content_catalog import CatalogEntry

    work_key = f"{kind}-{number:02d}"
    return CatalogEntry(
        kind=cast(Any, kind),
        number=number,
        slug=work_key,
        title=f"{kind} {number}",
        lang=cast(Any, lang),
        description="",
        work_key=work_key,
        work_dir=Path("src/content") / f"{kind}s" / work_key,
        md_path=Path("src/content") / f"{kind}s" / work_key / f"{lang}.md",
        frontmatter={"kind": kind, "number": number, "lang": lang},
    )


# --- the navigable ontology (--help at every level exits 0) -------------------
@pytest.mark.parametrize(
    "argv",
    [
        ["--help"],
        ["conceptosphere", "--help"],
        ["conceptosphere", "graph", "--help"],
        ["conceptosphere", "embed", "--help"],
    ],
)
def test_help_exits_zero(argv: list[str]) -> None:
    assert _exit_code(argv) == 0


# --- usage errors are exit 2 --------------------------------------------------
@pytest.mark.parametrize("argv", [[], ["conceptosphere"], ["conceptosphere", "graph"], ["conceptosphere", "embed"]])
def test_bare_group_or_noun_is_usage_error(argv: list[str]) -> None:
    assert _exit_code(argv) == 2


@pytest.mark.parametrize("argv", [["bogus"], ["conceptosphere", "bogus"], ["conceptosphere", "graph", "bogus"]])
def test_unknown_command_is_usage_error(argv: list[str]) -> None:
    assert _exit_code(argv) == 2


@pytest.mark.parametrize("argv", [["work", "cover"], ["work", "cover", "1"]])
def test_retired_work_cover_is_usage_error(argv: list[str]) -> None:
    assert _exit_code(argv) == 2


@pytest.mark.parametrize(
    "verify", [["audit"], ["site"], ["check"], ["build"], ["test"], ["dev"], ["preview"]]
)
def test_door_has_no_verify_verb(verify: list[str]) -> None:
    """The door MUTATES; the site-door verb family (verify: audit/check/test;
    build/serve: build/dev/preview; plus the `site` proxy) is the npm door's job
    (the mutate/verify cut, PAN019). None are door groups → usage error."""
    assert _exit_code(verify) == 2


# --- heavy verbs behind the extras gate --------------------------------------
def test_light_core_imports_no_ml_deps() -> None:
    """Importing the door must not pull a heavy stack — the light core stays light.
    `import pancratius.cli` ran at module load; assert no heavy module rode in."""
    for heavy in ("conceptosphere", "conceptosphere_embed", "networkx", "igraph", "mlx", "numpy"):
        assert heavy not in sys.modules, f"light core unexpectedly imported {heavy}"


@pytest.mark.parametrize(
    ("argv", "owner", "extra"),
    [
        (["conceptosphere", "graph", "generate"], "pancratius.conceptosphere", "graph"),
        (["conceptosphere", "embed", "generate"], "pancratius.conceptosphere_embed", "embed"),
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

    def stub(**kwargs: object) -> None:
        calls.append(kwargs)

    fake = types.ModuleType(module)
    setattr(fake, attr, stub)
    if module == "pancratius.conceptosphere":
        fake_dynamic = cast(Any, fake)
        fake_dynamic.GraphConfig = types.SimpleNamespace
        fake_dynamic.GraphGenerationError = RuntimeError
    monkeypatch.setitem(sys.modules, module, fake)
    return calls


def test_conceptosphere_graph_generate_dispatches_with_only(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_owner(monkeypatch, "pancratius.conceptosphere", "generate_graph")
    assert _exit_code(["conceptosphere", "graph", "generate", "--only", "books"]) == 0
    assert calls[0]["only"] == "books"
    assert isinstance(calls[0]["config"], types.SimpleNamespace)
    assert calls[0]["concepts_out"] is None
    assert calls[0]["books_out"] is None
    assert calls[0]["quiet"] is False


def test_conceptosphere_graph_generate_defaults_to_both(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_owner(monkeypatch, "pancratius.conceptosphere", "generate_graph")
    assert _exit_code(["conceptosphere", "graph", "generate"]) == 0
    assert calls[0]["only"] is None  # only=None → both projections


def test_conceptosphere_embed_generate_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_owner(monkeypatch, "pancratius.conceptosphere_embed", "generate_embeddings")
    assert _exit_code(["conceptosphere", "embed", "generate"]) == 0
    assert calls == [
        {
            "model": "Qwen/Qwen3-Embedding-0.6B",
            "rebuild": False,
            "batch_size": 8,
            "max_length": 512,
            "out": Path("data/conceptosphere-embed.json"),
            "limit": 0,
        }
    ]


# --- work import (writer-backed; door owns the report output) -----------------
def _fake_report(*, refused: tuple[object, ...] = ()) -> types.SimpleNamespace:
    """A stand-in for lib.writer.WriteReport carrying the fields print_report reads."""
    return types.SimpleNamespace(
        created=(), changed=(), skipped=(), refused=refused, diagnostics=()
    )


def test_work_import_builds_request_and_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    """The door maps flags into ImportRequest and calls import_work; a clean
    report exits 0."""
    from pancratius import import_docx

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
    assert isinstance(req.target, import_docx.NewWorkTarget)
    assert req.target.kind == "book"
    assert req.lang == "ru"
    assert req.write.dry_run is True
    assert req.docx == Path("x.docx")


def test_work_import_to_selector_builds_explicit_target(monkeypatch: pytest.MonkeyPatch) -> None:
    from pancratius import import_docx

    monkeypatch.setattr(cli.shutil, "which", lambda _tool: "/usr/bin/pandoc")
    captured: list[object] = []

    def fake_import_work(request: object) -> object:
        captured.append(request)
        return _fake_report()

    monkeypatch.setattr(import_docx, "import_work", fake_import_work)
    rc = _exit_code(["work", "import", "x.docx", "--to", "book:50", "--lang", "ru", "--replace"])
    assert rc == 0
    (req,) = captured
    assert isinstance(req, import_docx.ImportRequest)
    assert isinstance(req.target, import_docx.ExplicitWorkTarget)
    assert req.target.kind == "book"
    assert req.target.number == 50
    assert req.write.replace is True


@pytest.mark.parametrize(
    "argv",
    [
        ["work", "import", "x.docx", "--into", "book-50", "--lang", "ru"],
        ["work", "import", "x.docx", "--kind", "book", "--number", "50", "--lang", "ru"],
    ],
)
def test_work_import_retires_raw_destination_flags(argv: list[str]) -> None:
    assert _exit_code(argv) == 2


def test_work_import_kind_and_to_are_mutually_exclusive(monkeypatch: pytest.MonkeyPatch) -> None:
    from pancratius import import_docx

    monkeypatch.setattr(cli.shutil, "which", lambda _tool: "/usr/bin/pandoc")

    dispatched = False

    def fake_import_work(_request: object) -> object:
        nonlocal dispatched
        dispatched = True
        return _fake_report()

    monkeypatch.setattr(import_docx, "import_work", fake_import_work)

    assert _exit_code(["work", "import", "x.docx", "--kind", "book", "--to", "book:50", "--lang", "ru"]) == 2
    assert dispatched is False


def test_work_import_refusal_is_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from pancratius import import_docx

    monkeypatch.setattr(cli.shutil, "which", lambda _tool: "/usr/bin/pandoc")
    monkeypatch.setattr(
        import_docx, "import_work", lambda _r: _fake_report(refused=("books/01-x/ru.md",))
    )
    assert _exit_code(["work", "import", "x.docx", "--kind", "book", "--lang", "ru"]) == 1


def test_work_import_input_error_is_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    """ImportWorkError (bad input / unresolvable target) is a usage error
    (exit 2), matching the public door."""
    from pancratius import import_docx

    monkeypatch.setattr(cli.shutil, "which", lambda _tool: "/usr/bin/pandoc")

    def boom(_r: object) -> object:
        raise import_docx.ImportWorkError("--kind is required when importing a new work")

    monkeypatch.setattr(import_docx, "import_work", boom)
    assert _exit_code(["work", "import", "x.docx", "--kind", "book", "--lang", "ru"]) == 2


def test_work_import_missing_kind_is_usage_before_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    from pancratius import import_docx

    monkeypatch.setattr(cli.shutil, "which", lambda _tool: "/usr/bin/pandoc")

    dispatched = False

    def fake_import_work(_request: object) -> object:
        nonlocal dispatched
        dispatched = True
        return _fake_report()

    monkeypatch.setattr(import_docx, "import_work", fake_import_work)

    assert _exit_code(["work", "import", "x.docx", "--lang", "ru"]) == 2
    assert dispatched is False


def test_work_import_missing_pandoc_is_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.shutil, "which", lambda _tool: None)
    assert _exit_code(["work", "import", "x.docx", "--kind", "book", "--lang", "ru"]) == 1


def test_work_translate_selectors_dispatch_in_user_order(monkeypatch: pytest.MonkeyPatch) -> None:
    from pancratius import content_catalog
    from pancratius.translation import text as xlate

    entries = [
        _catalog_entry(kind="book", number=1),
        _catalog_entry(kind="book", number=2),
        _catalog_entry(kind="poem", number=1),
    ]
    seen: list[int] = []

    class FakeOpenRouterClient:
        def __init__(self, *, api_key: str) -> None:
            self.api_key = api_key

    def fake_translate_book(_client: object, _config: object, *, entry: object, **_kwargs: object) -> object:
        entry_dynamic = cast(Any, entry)
        seen.append(entry_dynamic.number)
        return xlate.TranslationReport(
            book_key=entry_dynamic.work_key,
            units=1,
            chunks=1,
            outcome=xlate.TranslationEstimateOutcome(
                xlate.CostEstimate(
                    source_tokens=1000,
                    output_tokens=100,
                    reference_tokens=1000,
                    chunks=1,
                    draft_cost_usd=0.01,
                    revise_cost_usd=0.01,
                    profile_cost_usd=0.01,
                )
            ),
        )

    monkeypatch.setattr(content_catalog, "scan_catalog", lambda _root: entries)
    monkeypatch.setattr(xlate, "OpenRouterClient", FakeOpenRouterClient)
    monkeypatch.setattr(xlate, "load_tag_labels", lambda _path: {})
    monkeypatch.setattr(xlate, "translate_book", fake_translate_book)

    rc = _exit_code(["work", "translate", "book:2", "book:1", "book:2", "--dry-run", "--workers", "1"])

    assert rc == 0
    assert seen == [2, 1]


def test_work_translate_retires_book_and_kind_target_flags() -> None:
    assert _exit_code(["work", "translate", "--book", "3"]) == 2
    assert _exit_code(["work", "translate", "--kind", "book"]) == 2


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
    from pancratius import docx_conversion

    monkeypatch.setattr(cli.shutil, "which", lambda _tool: "/usr/bin/pandoc")
    captured: list[dict[str, object]] = []

    def fake_scaffold(**kwargs: object) -> object:
        captured.append(kwargs)
        return _fake_scaffold_report()

    monkeypatch.setattr(docx_conversion, "scaffold_subpage", fake_scaffold)
    rc = _exit_code(["project", "page", "add", "project:holy-rus/my-sub", "doc.docx", "--lang", "ru"])
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


def test_project_page_add_dry_run_omits_landing_suggestion(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The landing suggestion is a POST-write next step: --dry-run (which writes
    nothing) must not emit it, only the planned-write-set preview."""
    from pancratius import docx_conversion

    monkeypatch.setattr(cli.shutil, "which", lambda _tool: "/usr/bin/pandoc")
    monkeypatch.setattr(docx_conversion, "scaffold_subpage", lambda **_k: _fake_scaffold_report())
    rc = _exit_code(
        ["project", "page", "add", "project:holy-rus/my-sub", "doc.docx", "--lang", "ru", "--dry-run"]
    )
    assert rc == 0
    assert "add this entry" not in capsys.readouterr().out


def test_project_page_add_refusal_is_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from pancratius import docx_conversion

    monkeypatch.setattr(cli.shutil, "which", lambda _tool: "/usr/bin/pandoc")
    monkeypatch.setattr(
        docx_conversion,
        "scaffold_subpage",
        lambda **_k: _fake_scaffold_report(refused=("projects/holy-rus/subpages/my-sub/ru.md",)),
    )
    assert _exit_code(["project", "page", "add", "project:holy-rus/my-sub", "x.docx", "--lang", "ru"]) == 1


def test_project_page_add_scaffold_error_is_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ScaffoldError (bad input — missing/non-DOCX) is a usage error (exit 2)."""
    from pancratius import docx_conversion

    monkeypatch.setattr(cli.shutil, "which", lambda _tool: "/usr/bin/pandoc")

    def boom(**_k: object) -> object:
        raise docx_conversion.ScaffoldError("expected a .docx file")

    monkeypatch.setattr(docx_conversion, "scaffold_subpage", boom)
    assert _exit_code(["project", "page", "add", "project:holy-rus/my-sub", "x.txt", "--lang", "ru"]) == 2


def test_project_page_add_missing_pandoc_is_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.shutil, "which", lambda _tool: None)
    assert _exit_code(["project", "page", "add", "project:holy-rus/my-sub", "x.docx", "--lang", "ru"]) == 1


@pytest.mark.parametrize(
    "destination",
    [
        "project:holy-rus",
        "project:holy_rus/my-sub",
        "project:holy-rus/my_sub",
        "project:holy-rus/../escape",
    ],
)
def test_project_page_add_requires_subpage_selector(
    monkeypatch: pytest.MonkeyPatch, destination: str
) -> None:
    monkeypatch.setattr(cli.shutil, "which", lambda _tool: None)
    assert _exit_code(["project", "page", "add", destination, "x.docx", "--lang", "ru"]) == 2


def test_project_page_add_retires_split_project_and_subpage_args(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _exit_code(["project", "page", "add", "holy-rus", "my-sub", "x.docx", "--lang", "ru"]) == 2
    assert "project:holy-rus/my-sub <docx>" in capsys.readouterr().err


# --- downloads / docx ---------------------------------------------------------
def test_downloads_render_dry_run_json_reports_plan(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from pancratius import render_downloads

    book_2 = render_downloads.WorkEntry(
        kind=cast(Any, "book"),
        number=2,
        folder=Path("books/book-02"),
        lang=cast(Any, "ru"),
        md=Path("books/book-02/ru.md"),
        slug="book-02",
        title="Book 2",
    )
    poem_1 = render_downloads.WorkEntry(
        kind=cast(Any, "poem"),
        number=1,
        folder=Path("poetry/poem-01"),
        lang=cast(Any, "ru"),
        md=Path("poetry/poem-01/ru.md"),
        slug="poem-01",
        title="Poem 1",
    )
    plan = render_downloads.RenderPlan(
        actions=(
            render_downloads.RenderAction(
                entry=book_2,
                format="pdf",
                output=Path("books/book-02/ru.pdf"),
                action="render",
                reason="missing-or-stale",
            ),
        )
    )

    seen: list[dict[str, object]] = []

    def fake_build_plan(**kwargs: object) -> render_downloads.RenderPlan:
        seen.append(kwargs)
        return plan

    monkeypatch.setattr(render_downloads, "discover_works", lambda: [book_2, poem_1])
    monkeypatch.setattr(render_downloads, "build_plan", fake_build_plan)
    monkeypatch.setattr(render_downloads, "execute_plan", lambda _plan: pytest.fail("executed dry-run"))
    assert _exit_code(["downloads", "render", "poem:1", "book:2", "--dry-run", "--json"]) == 0
    assert seen and seen[0]["entries"] == (poem_1, book_2)

    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["summary"] == {
        "pdfs_planned": 1,
        "epubs_planned": 0,
        "pdfs_made": 0,
        "epubs_made": 0,
        "skipped": 0,
    }
    assert payload["actions"][0]["action"] == "render"
    assert payload["actions"][0]["output"] == "books/book-02/ru.pdf"


def test_downloads_render_json_reports_execution_without_progress(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from pancratius import render_downloads

    book_2 = render_downloads.WorkEntry(
        kind=cast(Any, "book"),
        number=2,
        folder=Path("books/book-02"),
        lang=cast(Any, "ru"),
        md=Path("books/book-02/ru.md"),
        slug="book-02",
        title="Book 2",
    )
    plan = render_downloads.RenderPlan(
        actions=(
            render_downloads.RenderAction(
                entry=book_2,
                format="pdf",
                output=Path("books/book-02/ru.pdf"),
                action="render",
                reason="missing-or-stale",
            ),
            render_downloads.RenderAction(
                entry=book_2,
                format="epub",
                output=Path("books/book-02/ru.epub"),
                action="skip",
                reason="up-to-date",
            ),
        )
    )

    monkeypatch.setattr(render_downloads, "discover_works", lambda: [book_2])
    monkeypatch.setattr(render_downloads, "build_plan", lambda **_kwargs: plan)
    monkeypatch.setattr(
        render_downloads,
        "execute_plan",
        lambda _plan: render_downloads.RenderSummary(pdfs_made=1, epubs_made=0, skipped=1),
    )

    assert _exit_code(["downloads", "render", "book:2", "--json"]) == 0

    stdout = capsys.readouterr().out
    payload = json.loads(stdout)
    assert "download render result:" not in stdout
    assert payload["dry_run"] is False
    assert payload["summary"]["pdfs_made"] == 1
    assert [action["action"] for action in payload["actions"]] == ["rendered", "skipped"]


def test_downloads_render_missing_explicit_selector_is_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pancratius import render_downloads

    monkeypatch.setattr(render_downloads, "discover_works", lambda: [])
    assert _exit_code(["downloads", "render", "book:999"]) == 1


@pytest.mark.parametrize("selector", ["book:x", "book-3", "project:holy-rus", "book:0"])
def test_downloads_render_malformed_selector_is_usage(selector: str) -> None:
    assert _exit_code(["downloads", "render", selector]) == 2


def test_downloads_render_retires_target_flags() -> None:
    assert _exit_code(["downloads", "render", "--book", "3"]) == 2
    assert _exit_code(["downloads", "render", "--poem", "1"]) == 2


def test_docx_optimize_dispatches_with_path_objects(monkeypatch: pytest.MonkeyPatch) -> None:
    from pancratius import docx_optimize

    seen: list[dict[str, object]] = []

    def fake_optimize(**kwargs: object) -> docx_optimize.OptimizeSummary:
        seen.append(kwargs)
        return docx_optimize.OptimizeSummary(processed=0, skipped=0, failed=0, dry_run=True)

    monkeypatch.setattr(docx_optimize, "optimize", fake_optimize)
    assert _exit_code(["docx", "optimize", "a.docx", "--dry-run"]) == 0
    assert seen and seen[0]["paths"] == [Path("a.docx")] and seen[0]["dry_run"] is True


def _ok_image_result(key: str) -> object:
    from pancratius.translation.image.models import ImageTranslationResult, ImageTranslationStatus

    return ImageTranslationResult(
        key=key,
        status=ImageTranslationStatus.OK,
        final_path=Path(f"{key}.en.png"),
        raw_path=Path(f"{key}.raw.png"),
        attempts=(),
        primary_text=None,
        error=None,
        total_cost_usd=0.0,
        metadata={"kind": "test-image"},
    )


def test_image_translate_dry_run_json_needs_no_api_or_translator(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from pancratius.translation.image import client as image_client
    from pancratius.translation.image import translator as image_translator
    from pancratius.translation.image.models import ImageTranslationJob
    from pancratius.translation.image.providers import ProviderJob, book_cover

    class FakeBookCoverProvider:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def spec(self, selector: str) -> ProviderJob:
            return ProviderJob(
                job=ImageTranslationJob(
                    key=selector,
                    source_image=Path(f"{selector}.ru.png"),
                    target_image=Path(f"{selector}.en.png"),
                    raw_image=Path(f"{selector}.raw.png"),
                    metadata={"kind": "book-cover"},
                ),
                label=selector,
            )

    monkeypatch.setattr(book_cover, "BookCoverProvider", FakeBookCoverProvider)
    monkeypatch.setattr(image_client, "api_key_from_env", lambda: pytest.fail("api key requested"))
    monkeypatch.setattr(image_translator, "ImageTextTranslator", lambda **_kwargs: pytest.fail("translator built"))

    assert _exit_code(["image", "translate", "book:1", "--dry-run", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["summary"] == {"images": 1}
    assert payload["images"][0]["selector"] == "book-01"
    assert payload["images"][0]["source_image"] == "book-01.ru.png"
    assert payload["images"][0]["target_image"] == "book-01.en.png"


def test_image_translate_project_dry_run_json_uses_central_selector(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from pancratius.translation.image import client as image_client
    from pancratius.translation.image import translator as image_translator
    from pancratius.translation.image.models import ImageTranslationJob
    from pancratius.translation.image.providers import ProviderJob, project_cover

    seen: list[str] = []

    class FakeProjectCoverProvider:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def spec(self, selector: object) -> ProviderJob:
            seen.append(f"{selector!s}")
            return ProviderJob(
                job=ImageTranslationJob(
                    key=f"{selector!s}",
                    source_image=Path("cover.ru.png"),
                    target_image=Path("cover.en.png"),
                    raw_image=Path("project-holy-rus-tartaria.raw.png"),
                    metadata={"kind": "project-cover"},
                ),
                label=f"{selector!s}",
            )

    monkeypatch.setattr(project_cover, "ProjectCoverProvider", FakeProjectCoverProvider)
    monkeypatch.setattr(image_client, "api_key_from_env", lambda: pytest.fail("api key requested"))
    monkeypatch.setattr(image_translator, "ImageTextTranslator", lambda **_kwargs: pytest.fail("translator built"))

    assert _exit_code(["image", "translate", "project:holy-rus/tartaria", "--dry-run", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert seen == ["project:holy-rus/tartaria"]
    assert payload["images"][0]["selector"] == "project:holy-rus/tartaria"
    assert payload["images"][0]["kind"] == "project-cover"


def test_image_translate_help_hides_provider_path_flags(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _exit_code(["image", "translate", "--help"]) == 0
    out = capsys.readouterr().out
    assert "--dry-run" in out
    assert "--replace" in out
    assert "--output-dir" in out
    assert "--covers-dir" not in out
    assert "--queue-md" not in out
    assert "--books-root" not in out
    assert "--seed" not in out
    assert "--content-root" not in out


def test_image_translate_replace_passes_engine_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    from pancratius.translation.image import client as image_client
    from pancratius.translation.image import translator as image_translator
    from pancratius.translation.image.models import ImageTranslationJob
    from pancratius.translation.image.providers import ProviderJob, book_cover
    from pancratius.translation.image.translator import ImageTranslationConfig

    seen_replace: list[bool] = []

    class FakeBookCoverProvider:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def spec(self, selector: str) -> ProviderJob:
            return ProviderJob(
                job=ImageTranslationJob(
                    key=selector,
                    source_image=Path(f"{selector}.ru.png"),
                    target_image=Path(f"{selector}.en.png"),
                    raw_image=Path(f"{selector}.raw.png"),
                    metadata={"kind": "book-cover"},
                ),
                label=selector,
            )

    class FakeImageTextTranslator:
        def __init__(self, *, config: ImageTranslationConfig, **_kwargs: object) -> None:
            seen_replace.append(bool(config.replace_existing))

        def translate(self, job: ImageTranslationJob) -> object:
            return _ok_image_result(job.key)

    monkeypatch.setattr(image_client, "api_key_from_env", lambda: "fake-key")
    monkeypatch.setattr(book_cover, "BookCoverProvider", FakeBookCoverProvider)
    monkeypatch.setattr(image_translator, "ImageTextTranslator", FakeImageTextTranslator)

    assert _exit_code(["image", "translate", "book:1", "--replace"]) == 0
    assert seen_replace == [True]


def test_image_translate_existing_target_refusal_output_points_to_replace(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from pancratius.translation.image import client as image_client
    from pancratius.translation.image import translator as image_translator
    from pancratius.translation.image.models import (
        AttemptRecord,
        GenerationCost,
        ImageTranslationJob,
        ImageTranslationResult,
        ImageTranslationStatus,
        QaDiscrepancy,
        QaResult,
        QaVerdict,
    )
    from pancratius.translation.image.providers import ProviderJob, book_cover

    class FakeBookCoverProvider:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def spec(self, selector: str) -> ProviderJob:
            return ProviderJob(
                job=ImageTranslationJob(
                    key=selector,
                    source_image=Path(f"{selector}.ru.png"),
                    target_image=Path(f"{selector}.en.png"),
                    raw_image=Path(f"{selector}.raw.png"),
                    metadata={"kind": "book-cover"},
                ),
                label=selector,
            )

    class FakeImageTextTranslator:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def translate(self, job: ImageTranslationJob) -> object:
            qa = QaResult(
                verdict=QaVerdict.FAIL,
                discrepancies=(QaDiscrepancy(kind="wrong_text", description="wrong text"),),
                raw_json="{}",
            )
            return ImageTranslationResult(
                key=job.key,
                status=ImageTranslationStatus.FAIL,
                final_path=None,
                raw_path=None,
                attempts=(AttemptRecord(0, qa, GenerationCost(0.0), ""),),
                primary_text=None,
                error="existing target failed QA; pass --replace to regenerate it",
                total_cost_usd=0.0,
                metadata={"kind": "book-cover"},
            )

    monkeypatch.setattr(image_client, "api_key_from_env", lambda: "fake-key")
    monkeypatch.setattr(book_cover, "BookCoverProvider", FakeBookCoverProvider)
    monkeypatch.setattr(image_translator, "ImageTextTranslator", FakeImageTextTranslator)

    assert _exit_code(["image", "translate", "book:1"]) == 1
    out = capsys.readouterr().out
    assert "QA:FAIL(existing)" in out
    assert "pass --replace" in out
    assert "existing image passed QA" not in out


def test_image_translate_item_exception_is_batch_failure_and_continues(monkeypatch: pytest.MonkeyPatch) -> None:
    from pancratius.translation.image import client as image_client
    from pancratius.translation.image import translator as image_translator
    from pancratius.translation.image.models import ImageTranslationJob
    from pancratius.translation.image.providers import ProviderJob, book_cover

    jobs: dict[str, ImageTranslationJob] = {}
    calls: list[str] = []

    class FakeBookCoverProvider:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def spec(self, selector: str) -> ProviderJob:
            key = selector
            job = ImageTranslationJob(
                key=key,
                source_image=Path(f"{key}.ru.png"),
                target_image=Path(f"{key}.en.png"),
                raw_image=Path(f"{key}.raw.png"),
                metadata={"kind": "book-cover"},
            )
            jobs[key] = job
            return ProviderJob(job=job, label=key)

    class FakeImageTextTranslator:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def translate(self, job: ImageTranslationJob) -> object:
            calls.append(job.key)
            if job.key == "book-01":
                raise RuntimeError("bad image")
            return _ok_image_result(job.key)

    monkeypatch.setattr(image_client, "api_key_from_env", lambda: "fake-key")
    monkeypatch.setattr(book_cover, "BookCoverProvider", FakeBookCoverProvider)
    monkeypatch.setattr(image_translator, "ImageTextTranslator", FakeImageTextTranslator)

    assert _exit_code(["image", "translate", "book:1", "book:2"]) == 1
    assert calls == ["book-01", "book-02"]
    assert set(jobs) == {"book-01", "book-02"}


def test_image_translate_insufficient_credits_stops_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    from pancratius.translation.image import client as image_client
    from pancratius.translation.image import translator as image_translator
    from pancratius.translation.image.models import ImageTranslationJob
    from pancratius.translation.image.providers import ProviderJob, book_cover

    calls: list[str] = []

    class FakeBookCoverProvider:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def spec(self, selector: str) -> ProviderJob:
            key = selector
            return ProviderJob(
                job=ImageTranslationJob(
                    key=key,
                    source_image=Path(f"{key}.ru.png"),
                    target_image=Path(f"{key}.en.png"),
                    raw_image=Path(f"{key}.raw.png"),
                    metadata={"kind": "book-cover"},
                ),
                label=key,
            )

    class FakeImageTextTranslator:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def translate(self, job: ImageTranslationJob) -> object:
            calls.append(job.key)
            raise image_client.InsufficientCreditsError("HTTP 402: no credits")

    monkeypatch.setattr(image_client, "api_key_from_env", lambda: "fake-key")
    monkeypatch.setattr(book_cover, "BookCoverProvider", FakeBookCoverProvider)
    monkeypatch.setattr(image_translator, "ImageTextTranslator", FakeImageTextTranslator)

    assert _exit_code(["image", "translate", "book:1", "book:2"]) == 1
    assert calls == ["book-01"]


def test_image_translate_rejects_legacy_book_keys() -> None:
    assert _exit_code(["image", "translate", "book-1"]) == 2
    assert _exit_code(["image", "translate", "1"]) == 2


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
            "--to", "book:91", "--lang", "ru",
            "--slug", "door-probe",
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
            "project", "page", "add", "project:holy-rus/classification", str(_FIXTURE_DOCX),
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
    from pancratius.content_catalog import split_frontmatter

    content_root = tmp_path / "src" / "content"
    rc = cli.main(
        [
            "project", "page", "add", "project:holy-rus/classification", str(_FIXTURE_DOCX),
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
    from pancratius.docx_conversion import scaffold_subpage

    content_root = tmp_path / "src" / "content"
    report = scaffold_subpage(
        project=project, subpage_slug=subpage_slug, docx=_FIXTURE_DOCX, lang="ru",
        out_content=content_root, dry_run=True,
    )
    assert report.refused, "a scope-escaping arg must be refused"
    assert any(d.severity == "fatal" for d in report.diagnostics)
