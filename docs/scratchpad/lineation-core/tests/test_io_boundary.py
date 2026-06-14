# research-pure: the write boundary is enforced here, not just documented.
"""Disk writes live only in `artifact` (cache), `store` (truth/evidence), `corrections` (the one
write into production content). This test fails on a write anywhere else, so the boundary moves by
editing `SANCTIONED` — a reviewed decision, never an accident."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src" / "lineation_core"

# The only modules permitted to MUTATE disk (relative to _SRC). Moving the boundary is a
# deliberate edit to this set — reviewed, not silent.
SANCTIONED = {"artifact.py", "store.py", "corrections.py"}

# Path/os/shutil mutators. Names unique to filesystem objects (never str/list methods), so an
# attribute-name match is safe; `open(...)` is checked separately for a write mode.
_PATH_MUTATORS = {"write_text", "write_bytes", "unlink", "mkdir", "rmdir"}
_OS_MUTATORS = {"replace", "remove", "rename", "removedirs", "rmdir"}      # os.<name>
_SHUTIL_MUTATORS = {"move", "copy", "copy2", "copyfile", "copytree", "rmtree"}  # shutil.<name>


def _attr_root(node: ast.AST) -> str | None:
    """The leftmost Name of an attribute chain (`os.replace` -> 'os'), else None."""
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _is_write_open(call: ast.Call) -> bool:
    """`open(path, 'w'|'a'|'x'|'r+'|...)` — a write-capable mode in the 2nd positional or `mode=`."""
    func = call.func
    if not (isinstance(func, ast.Name) and func.id == "open"):
        return False
    mode = call.args[1] if len(call.args) >= 2 else next(
        (k.value for k in call.keywords if k.arg == "mode"), None)
    return isinstance(mode, ast.Constant) and isinstance(mode.value, str) \
        and any(c in mode.value for c in "wax+")


def _mutations(tree: ast.AST) -> list[tuple[int, str]]:
    """Every disk-mutation call in one module: (lineno, what)."""
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Attribute):
            root = _attr_root(f)
            if f.attr in _PATH_MUTATORS:
                out.append((node.lineno, f"<path>.{f.attr}()"))
            elif root == "os" and f.attr in _OS_MUTATORS:
                out.append((node.lineno, f"os.{f.attr}()"))
            elif root == "shutil" and f.attr in _SHUTIL_MUTATORS:
                out.append((node.lineno, f"shutil.{f.attr}()"))
        elif _is_write_open(node):
            out.append((node.lineno, "open(..., write-mode)"))
    return out


def _modules() -> list[Path]:
    return sorted(p for p in _SRC.rglob("*.py") if "vendor" not in p.parts)


def test_disk_writes_are_confined_to_the_sanctioned_modules() -> None:
    offenders: dict[str, list[tuple[int, str]]] = {}
    for path in _modules():
        rel = path.relative_to(_SRC).as_posix()
        if rel in SANCTIONED:
            continue
        muts = _mutations(ast.parse(path.read_text(), filename=str(path)))
        if muts:
            offenders[rel] = muts
    assert not offenders, (
        "disk mutation found outside the sanctioned writers "
        f"{sorted(SANCTIONED)} — route the write through `store`/`artifact` (our disk) or "
        f"`corrections` (production), or add the module to SANCTIONED on purpose:\n"
        + "\n".join(f"  {m}: {', '.join(f'{ln}:{what}' for ln, what in calls)}"
                    for m, calls in sorted(offenders.items())))


@pytest.mark.parametrize("name", sorted(SANCTIONED))
def test_each_sanctioned_writer_actually_writes(name: str) -> None:
    """Keep the allow-list HONEST: a sanctioned module that no longer writes should be removed
    from `SANCTIONED`, so the set always reflects the real write surface (no dead permissions)."""
    assert _mutations(ast.parse((_SRC / name).read_text())), \
        f"{name} is in SANCTIONED but performs no disk mutation — drop it from the allow-list"
