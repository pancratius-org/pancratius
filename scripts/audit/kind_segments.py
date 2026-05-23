#!/usr/bin/env -S uv run --quiet
"""Cross-language guard for the work-kind -> URL-segment mapping.

The mapping is intentionally defined twice — once in TypeScript
(``src/lib/kinds.ts``, the source for routes/config) and once in Python
(``scripts/lib/kinds.py``, the source for build scripts). Neither language can
import the other, so this audit is what keeps the two copies in agreement: it
parses ``SEGMENT_OF`` out of the TS module and asserts it equals the Python
``SEGMENT_OF`` dict.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
TS_KINDS = ROOT / "src" / "lib" / "kinds.ts"
PY_KINDS = ROOT / "scripts" / "lib" / "kinds.py"

# Matches `book: "books",` style entries inside the SEGMENT_OF object literal.
# Anchored on the `const SEGMENT_OF` declaration (not a bare `SEGMENT_OF`
# token) so a comment mention or a reordered `KIND_OF_SEGMENT` literal can't be
# captured by mistake.
_TS_BLOCK_RE = re.compile(r"\bconst\s+SEGMENT_OF\b[^=]*=\s*\{(.*?)\}", re.DOTALL)
_TS_ENTRY_RE = re.compile(r"(\w+)\s*:\s*['\"]([^'\"]+)['\"]")


def _parse_ts_segment_of(text: str) -> dict[str, str]:
    block = _TS_BLOCK_RE.search(text)
    if not block:
        raise ValueError("could not find SEGMENT_OF object literal in kinds.ts")
    return {m.group(1): m.group(2) for m in _TS_ENTRY_RE.finditer(block.group(1))}


def _load_py_segment_of() -> dict[str, str]:
    spec = importlib.util.spec_from_file_location("pancratius_kinds", PY_KINDS)
    if spec is None or spec.loader is None:
        raise ValueError(f"could not load {PY_KINDS}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return dict(module.SEGMENT_OF)


def main() -> int:
    if not TS_KINDS.exists():
        print(f"FAIL: missing {TS_KINDS.relative_to(ROOT)}", file=sys.stderr)
        return 1
    if not PY_KINDS.exists():
        print(f"FAIL: missing {PY_KINDS.relative_to(ROOT)}", file=sys.stderr)
        return 1

    ts_map = _parse_ts_segment_of(TS_KINDS.read_text(encoding="utf-8"))
    py_map = _load_py_segment_of()

    if ts_map != py_map:
        print("FAIL: kind segment maps drifted between TS and Python", file=sys.stderr)
        print(f"  {TS_KINDS.relative_to(ROOT)}: {ts_map}", file=sys.stderr)
        print(f"  {PY_KINDS.relative_to(ROOT)}: {py_map}", file=sys.stderr)
        return 1

    print(f"PASS: kind segment maps agree ({len(ts_map)} kinds)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
