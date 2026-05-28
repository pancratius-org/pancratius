from __future__ import annotations

"""Shared filesystem boundaries for the local corpus tooling.

The package owns one repository-shaped corpus. Callers may override the root with
``PANCRATIUS_ROOT``; otherwise the root is discovered from the current working
directory. Package assets are resolved relative to the package itself.
"""

from collections.abc import Iterator
import os
from pathlib import Path


def _candidate_roots(start: Path) -> Iterator[Path]:
    resolved = start.expanduser().resolve()
    yield resolved
    yield from resolved.parents


def _is_repo_root(path: Path) -> bool:
    return (path / "pyproject.toml").is_file() and (path / "src" / "content").is_dir()


def _discover_repo_root() -> Path:
    env = os.environ.get("PANCRATIUS_ROOT")
    if env:
        root = Path(env).expanduser().resolve()
        if not _is_repo_root(root):
            raise RuntimeError(f"PANCRATIUS_ROOT is not a Pancratius repo root: {root}")
        return root

    package_parent = Path(__file__).resolve().parent.parent
    for start in (Path.cwd(), package_parent):
        for candidate in _candidate_roots(start):
            if _is_repo_root(candidate):
                return candidate
    raise RuntimeError("could not locate the Pancratius repo root; set PANCRATIUS_ROOT")


REPO_ROOT = _discover_repo_root()
CONTENT_ROOT = REPO_ROOT / "src" / "content"
DATA_ROOT = REPO_ROOT / "data"
CACHE_ROOT = REPO_ROOT / ".cache"

PACKAGE_ROOT = Path(__file__).resolve().parent
DOWNLOAD_TEMPLATES_ROOT = PACKAGE_ROOT / "download_assets" / "templates"
DOWNLOAD_FONTS_ROOT = PACKAGE_ROOT / "download_assets" / "fonts"


def data_root_for_content_root(root: Path) -> Path:
    resolved = root.expanduser().resolve()
    if resolved.name != "content" or resolved.parent.name != "src":
        raise ValueError(
            f"--out-content {root} must be shaped like '<root>/src/content' "
            "to locate the sibling data/ tree"
        )
    return resolved.parent.parent / "data"


def imports_dir_for_content_root(root: Path) -> Path:
    return data_root_for_content_root(root) / "imports"
