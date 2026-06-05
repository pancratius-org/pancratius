# research-pure: test bootstrap — put the package src on the path; require truth + record cache.
"""Makes `import lineation_core` resolve and asserts both halves of the store are present:

  - the committed annotation TRUTH (`annotations/`) — source data, never rebuilt;
  - the derived record CACHE (`_artifacts/`) — rebuilt from the committed DOCX by `build_records`.

The package is LOAD-ONLY: every consumer reads these and fails loud if missing — it never
rebuilds on the fly. A missing half is a setup error surfaced here once, not as N opaque
per-test failures."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _require_store() -> None:
    from lineation_core import artifact, paths

    if not (paths.ANNOTATIONS / artifact.LABELS_FILE).is_file():
        raise RuntimeError(
            f"committed annotation truth missing at {paths.ANNOTATIONS} — it is source data, not "
            f"rebuilt; restore it before running the suite.")
    if not any(paths.ARTIFACT_STORE.glob(f"*/{artifact.RECORDS_FILE}")):
        raise RuntimeError(
            f"record cache missing at {paths.ARTIFACT_STORE} — run "
            f"`python -m lineation_core.build_records` to rebuild it from the committed DOCX.")


_require_store()
