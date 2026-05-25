"""Behavioural tests for the `pancratius` library door (docs/tooling.md).

These assert the *dispatch contract*: each verb routes to its owner entry, and the
door's uniform exit codes hold (0 ok / 1 refusal-or-failure / 2 usage). Owners are
monkeypatched so the door is tested in isolation — never against the real corpus.
The owners' own behaviour is covered by their dedicated tests.
"""

from __future__ import annotations

from pathlib import Path
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

    monkeypatch.setattr(build_slug_map, "main", lambda: 0)
    assert _exit_code(["data", "slug-map", "refresh"]) == 0


def test_owner_nonzero_collapses_to_one_not_two(monkeypatch: pytest.MonkeyPatch) -> None:
    """An owner's own nonzero return must surface as 1 (failure), never 2 — exit 2 is
    reserved for argparse usage so callers can distinguish a bad command from a
    failed one. build_slug_map returns 2 on dangling cross_refs; the door maps it."""
    import build_slug_map

    monkeypatch.setattr(build_slug_map, "main", lambda: 2)
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
