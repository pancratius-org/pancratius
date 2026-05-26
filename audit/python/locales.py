"""Cross-language guard for the canonical locale list and default locale.

The locale list is intentionally defined twice — once in TypeScript
(``src/lib/locales.ts``, the source for routes/config) and once in Python
(``pancratius/locales.py``, the source for corpus tooling). Neither language can
import the other, so this audit is what keeps the two copies in agreement: it
parses ``LOCALES`` and ``DEFAULT_LOCALE`` out of the TS module and asserts they
equal the Python ones (order included — the list order is the display order).

Mirrors ``audit/python/kind_segments.py``. Wrapped by the harness as
PAN003 (audit/rules/locales.ts); honours ``PANCRATIUS_AUDIT_ROOT``.
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
from pathlib import Path
from types import ModuleType


def _audit_root() -> Path:
    """The tree to scan: the fixture root when ``PANCRATIUS_AUDIT_ROOT`` is set
    (the harness points a wrapped check at a fixture that way), else the repo
    root."""
    env = os.environ.get("PANCRATIUS_AUDIT_ROOT")
    return Path(env).resolve() if env else Path(__file__).resolve().parents[2]


ROOT = _audit_root()
TS_LOCALES = ROOT / "src" / "lib" / "locales.ts"
PY_LOCALES = ROOT / "pancratius" / "locales.py"

# Matches `export const LOCALES = ["ru", "en"] as const;` and captures the
# bracketed body. Anchored on the `const LOCALES` declaration so a comment
# mention can't be captured by mistake.
_TS_LIST_RE = re.compile(r"\bconst\s+LOCALES\b[^=]*=\s*\[(.*?)\]", re.DOTALL)
_TS_ITEM_RE = re.compile(r"['\"]([^'\"]+)['\"]")
# Matches `export const DEFAULT_LOCALE: Locale = "ru";`.
_TS_DEFAULT_RE = re.compile(r"\bconst\s+DEFAULT_LOCALE\b[^=]*=\s*['\"]([^'\"]+)['\"]")


def _parse_ts_locales(text: str) -> list[str]:
    block = _TS_LIST_RE.search(text)
    if not block:
        raise ValueError("could not find LOCALES array literal in locales.ts")
    return [m.group(1) for m in _TS_ITEM_RE.finditer(block.group(1))]


def _parse_ts_default(text: str) -> str:
    m = _TS_DEFAULT_RE.search(text)
    if not m:
        raise ValueError("could not find DEFAULT_LOCALE in locales.ts")
    return m.group(1)


def _load_py_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("pancratius_locales", PY_LOCALES)
    if spec is None or spec.loader is None:
        raise ValueError(f"could not load {PY_LOCALES}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    if not TS_LOCALES.exists():
        print(f"FAIL: missing {TS_LOCALES.relative_to(ROOT)}", file=sys.stderr)
        return 1
    if not PY_LOCALES.exists():
        print(f"FAIL: missing {PY_LOCALES.relative_to(ROOT)}", file=sys.stderr)
        return 1

    ts_text = TS_LOCALES.read_text(encoding="utf-8")
    ts_locales = _parse_ts_locales(ts_text)
    ts_default = _parse_ts_default(ts_text)

    module = _load_py_module()
    py_locales = list(module.LOCALES)
    py_default = str(module.DEFAULT_LOCALE)

    ok = True
    if ts_locales != py_locales:
        print("FAIL: locale lists drifted between TS and Python", file=sys.stderr)
        print(f"  {TS_LOCALES.relative_to(ROOT)}: {ts_locales}", file=sys.stderr)
        print(f"  {PY_LOCALES.relative_to(ROOT)}: {py_locales}", file=sys.stderr)
        ok = False
    if ts_default != py_default:
        print("FAIL: default locale drifted between TS and Python", file=sys.stderr)
        print(f"  {TS_LOCALES.relative_to(ROOT)}: {ts_default!r}", file=sys.stderr)
        print(f"  {PY_LOCALES.relative_to(ROOT)}: {py_default!r}", file=sys.stderr)
        ok = False

    if not ok:
        return 1

    print(f"PASS: locale lists agree ({len(ts_locales)} locales, default {ts_default!r})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
