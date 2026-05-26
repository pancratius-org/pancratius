"""PURE unit tests for the WritePlan safety boundary (pancratius/writeplan.py).

No filesystem: `validate` takes its two filesystem questions as injected
predicates, so every rule is driven here with stubs. This is the boundary that
keeps the overwrite/scope policy unit-testable without a real tree.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import cast
import sys


from pancratius.writeplan import (  # noqa: E402
    AssetTransform,
    Diagnostic,
    OpKind,
    Role,
    WriteOp,
    WritePlan,
    has_fatal,
    validate,
)

SCOPE = PurePosixPath("books/99-probe")


def _plan(*ops: WriteOp, replace: bool = False) -> WritePlan:
    return WritePlan(
        target_root=Path("/unused"),  # never read — validate is pure
        target_scope=SCOPE,
        operations=ops,
        diagnostics=(),
        replace=replace,
    )


def _never(_rel: PurePosixPath) -> bool:
    return False


def _always(_rel: PurePosixPath) -> bool:
    return True


def _op(rel: str, *, kind: OpKind = "copy", role: Role = "imported_asset") -> WriteOp:
    return WriteOp(kind=kind, rel_path=PurePosixPath(rel), role=role, reason="t", source=Path("/x"))


def _codes(diags: tuple[Diagnostic, ...]) -> set[str]:
    return {d.code for d in diags}


def test_clean_plan_has_no_fatal() -> None:
    plan = _plan(
        _op("books/99-probe/ru.md", role="canonical_source"),
        _op("books/99-probe/images/a.png"),
    )
    diags = validate(plan, target_exists=_never, escapes_scope=_never)
    assert diags == ()
    assert not has_fatal(diags)


def test_absolute_path_is_rejected() -> None:
    plan = _plan(_op("/etc/passwd"))
    diags = validate(plan, target_exists=_never, escapes_scope=_never)
    assert "writeplan.unsafe-path" in _codes(diags)
    assert has_fatal(diags)


def test_parent_traversal_is_rejected() -> None:
    plan = _plan(_op("books/99-probe/../../escape.md"))
    diags = validate(plan, target_exists=_never, escapes_scope=_never)
    assert "writeplan.unsafe-path" in _codes(diags)


def test_out_of_scope_relpath_is_rejected() -> None:
    plan = _plan(_op("books/other-work/ru.md", role="canonical_source"))
    diags = validate(plan, target_exists=_never, escapes_scope=_never)
    assert "writeplan.out-of-scope" in _codes(diags)


def test_symlink_escape_is_rejected() -> None:
    # Lexically in-scope, but the injected escapes_scope stub says it resolves out.
    plan = _plan(_op("books/99-probe/ru.md", role="canonical_source"))
    diags = validate(plan, target_exists=_never, escapes_scope=_always)
    assert "writeplan.scope-escape" in _codes(diags)


def test_existing_canonical_source_without_replace_is_rejected() -> None:
    plan = _plan(_op("books/99-probe/ru.md", role="canonical_source"), replace=False)
    diags = validate(plan, target_exists=_always, escapes_scope=_never)
    assert "writeplan.overwrite-refused" in _codes(diags)


def test_existing_canonical_source_with_replace_is_accepted() -> None:
    plan = _plan(_op("books/99-probe/ru.md", role="canonical_source"), replace=True)
    diags = validate(plan, target_exists=_always, escapes_scope=_never)
    assert diags == ()


def test_existing_non_canonical_file_does_not_need_replace() -> None:
    # An existing imported asset / sidecar / artifact is regenerated freely; only
    # canonical_source <lang>.md is protected.
    plan = _plan(_op("books/99-probe/bibliography.yaml", role="sidecar"), replace=False)
    diags = validate(plan, target_exists=_always, escapes_scope=_never)
    assert diags == ()


def test_unknown_op_kind_is_rejected() -> None:
    # `delete` is not a valid OpKind by construction — a normal import never
    # deletes. The cast feeds the defensive bad-op-kind guard a value the type
    # system forbids, proving the runtime check still catches it.
    plan = _plan(_op("books/99-probe/ru.md", kind=cast(OpKind, "delete"), role="canonical_source"))
    diags = validate(plan, target_exists=_never, escapes_scope=_never)
    assert "writeplan.bad-op-kind" in _codes(diags)


def test_ensure_dir_scope_self_is_accepted() -> None:
    plan = _plan(WriteOp(kind="ensure_dir", rel_path=SCOPE, role="canonical_source", reason="dir"))
    diags = validate(plan, target_exists=_always, escapes_scope=_never)
    # The scope dir already existing must NOT trip the canonical-source overwrite
    # guard — adding a new language into an existing bundle is the normal case.
    assert diags == ()


def test_content_op_at_scope_dir_is_rejected() -> None:
    # `ensure_dir` at the scope is legitimate (above), but a write/copy whose
    # path IS the bundle directory would drop a file where the dir belongs.
    plan = _plan(_op("books/99-probe", kind="copy", role="canonical_source"))
    diags = validate(plan, target_exists=_never, escapes_scope=_never)
    assert "writeplan.scope-is-dir" in _codes(diags)
    assert has_fatal(diags)


# --- transform_asset gets the SAME path-safety treatment as copy/write_text. A
# transform_asset op has a real target file, so absolute paths, `..`, out-of-scope
# paths, scope-escapes, and a path that IS the scope dir must all be rejected. ---


def _transform_op(rel: str) -> WriteOp:
    return WriteOp(
        kind="transform_asset",
        rel_path=PurePosixPath(rel),
        role="imported_asset",
        reason="t",
        source=Path("/x"),
        transform=AssetTransform(kind="cap_raster", max_long_edge=1600),
    )


def test_transform_asset_clean_is_accepted() -> None:
    plan = _plan(_transform_op("books/99-probe/images/a.png"))
    diags = validate(plan, target_exists=_never, escapes_scope=_never)
    assert diags == ()
    assert not has_fatal(diags)


def test_transform_asset_absolute_path_is_rejected() -> None:
    plan = _plan(_transform_op("/etc/evil.png"))
    diags = validate(plan, target_exists=_never, escapes_scope=_never)
    assert "writeplan.unsafe-path" in _codes(diags)


def test_transform_asset_parent_traversal_is_rejected() -> None:
    plan = _plan(_transform_op("books/99-probe/../../escape.png"))
    diags = validate(plan, target_exists=_never, escapes_scope=_never)
    assert "writeplan.unsafe-path" in _codes(diags)


def test_transform_asset_out_of_scope_is_rejected() -> None:
    plan = _plan(_transform_op("books/other-work/images/a.png"))
    diags = validate(plan, target_exists=_never, escapes_scope=_never)
    assert "writeplan.out-of-scope" in _codes(diags)


def test_transform_asset_symlink_escape_is_rejected() -> None:
    plan = _plan(_transform_op("books/99-probe/images/a.png"))
    diags = validate(plan, target_exists=_never, escapes_scope=_always)
    assert "writeplan.scope-escape" in _codes(diags)


def test_transform_asset_at_scope_dir_is_rejected() -> None:
    plan = _plan(_transform_op("books/99-probe"))
    diags = validate(plan, target_exists=_never, escapes_scope=_never)
    assert "writeplan.scope-is-dir" in _codes(diags)
