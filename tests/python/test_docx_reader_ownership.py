"""Guard the raw DOCX seam: new package readers require an ownership decision."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# This is an executable ownership map, not a ban on legitimate package work.
# Adding a raw ZIP/XML/python-docx import must classify the new owner here, which
# prevents a semantic consumer from quietly growing a shadow source projection.
RAW_DOCX_PACKAGE_OWNERS = {
    "audit/docx_integrity.py": "independent package validator",
    "audit/python/download_asset_urls.py": "unrelated download-archive audit",
    "pancratius/docx_merge.py": "package merge and python-docx authoring",
    "pancratius/docx_optimize.py": "package mutation",
    "pancratius/docx_outline.py": "package authoring",
    "pancratius/docx_render.py": "independent source-slice renderer",
    "pancratius/docx_source.py": "sole semantic package reader",
    "pancratius/import_docx.py": "core-properties metadata reader",
    "pancratius/ooxml.py": "package-agnostic OOXML relationship helpers",
    "pancratius/translation/docx/audit.py": "translated-package validator",
    "pancratius/translation/docx/donor_docx.py": "translation donor package reader",
    "pancratius/translation/docx/models.py": "translation OOXML value types",
    "pancratius/translation/docx/ooxml_write.py": "translated-package writer",
    "pancratius/translation/docx/transfer.py": "Markdown-to-DOCX structure transfer",
}

RAW_PACKAGE_IMPORTS = ("docx", "lxml", "xml", "zipfile")
PRODUCTION_ROOTS = (ROOT / "pancratius", ROOT / "audit", ROOT / "intent-ai" / "src")


def _imports_raw_package_api(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names = (node.module,)
        else:
            continue
        if any(
            name == root or name.startswith(f"{root}.")
            for name in names
            for root in RAW_PACKAGE_IMPORTS
        ):
            return True
    return False


def test_every_raw_docx_package_consumer_has_one_classified_owner() -> None:
    discovered = {
        path.relative_to(ROOT).as_posix()
        for production_root in PRODUCTION_ROOTS
        for path in production_root.rglob("*.py")
        if _imports_raw_package_api(path)
    }

    assert discovered == RAW_DOCX_PACKAGE_OWNERS.keys()
    assert RAW_DOCX_PACKAGE_OWNERS["pancratius/docx_source.py"] == (
        "sole semantic package reader"
    )
