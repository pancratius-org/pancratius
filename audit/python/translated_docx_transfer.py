"""PAN025: committed translated-DOCX transfer artifact checks."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from pancratius.translation.docx.audit import audit_translated_docx_artifacts

ROOT = Path(os.environ.get("PANCRATIUS_AUDIT_ROOT", Path(__file__).resolve().parents[2]))


def main() -> int:
    audit = audit_translated_docx_artifacts(ROOT)
    if audit.failed:
        print(
            f"FAIL: {len(audit.issues)} translated DOCX transfer artifact issue(s)",
            file=sys.stderr,
        )
        for issue in audit.issues:
            print(f"  {issue.path}: {issue.message}", file=sys.stderr)
        return 1
    print(
        f"checked {audit.checked} translated DOCX artifact(s); "
        "package and footnote checks clean"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
