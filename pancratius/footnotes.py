# import-pure: no filesystem mutation
"""Final Markdown footnote-integrity diagnostics for the import pipeline.

The canonical source model keeps references and definitions linked until
lowering. This final check still refuses any unresolved marker, and warns about
unused or duplicate definitions, before the write plan can be applied.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Literal

type _Severity = Literal["fatal", "warning"]

# A footnote DEFINITION line `[^id]: body`, anchored at line start; `id` is the run
# of non-`]` characters, mirroring `lib.cross_refs._FOOTNOTE_LINE`.
_DEF_LINE_RE = re.compile(r"^\[\^([^\]]+)\]:\s?(.*)$")
# A footnote REFERENCE marker `[^id]` NOT followed by `:` (a `:` starts a definition).
_REF_RE = re.compile(r"\[\^([^\]]+)\](?!:)")


@dataclass(frozen=True)
class FootnoteDiagnostic:
    """A footnote finding: severity + stable code + human message.

    A plain value (not `writeplan.Diagnostic`) to keep this module free of
    import-pipeline coupling; the importer maps these onto `writeplan.Diagnostic`s.
    """

    severity: _Severity
    code: str
    message: str


def reference_ids(body: str) -> list[str]:
    """Every `[^id]` reference id in `body`, in order, with repeats."""
    return _REF_RE.findall(body)


def definition_ids(body: str) -> list[str]:
    """Every `[^id]:` definition id in `body`, in order, with repeats."""
    return [m.group(1) for m in (_DEF_LINE_RE.match(ln) for ln in body.splitlines()) if m]


def is_definition_line(line: str) -> bool:
    """Whether a Markdown line starts a footnote definition."""
    return _DEF_LINE_RE.match(line) is not None


def analyze_footnotes(body: str) -> list[FootnoteDiagnostic]:
    """Diagnose footnote integrity of a FINAL body markdown.

    * FATAL `import.footnote-unresolved` — an `[^id]` reference with NO matching
      `[^id]:` definition (the orphaned-marker bug class).
    * warning `import.footnote-unused` — a definition with no reference.
    * warning `import.footnote-duplicate` — a definition id defined more than once.

    Returns diagnostics in a stable order (unresolved, then unused, then
    duplicate; each sorted by id) so callers and tests are deterministic. An
    empty list means the body's footnotes are well-formed.
    """
    refs = reference_ids(body)
    defs = definition_ids(body)
    ref_set = set(refs)
    def_set = set(defs)

    diags: list[FootnoteDiagnostic] = []

    for fid in sorted(ref_set - def_set):
        diags.append(
            FootnoteDiagnostic(
                "fatal",
                "import.footnote-unresolved",
                f"footnote reference [^{fid}] has no matching [^{fid}]: definition "
                "(orphaned marker); the definition was lost during conversion.",
            )
        )

    for fid in sorted(def_set - ref_set):
        diags.append(
            FootnoteDiagnostic(
                "warning",
                "import.footnote-unused",
                f"footnote definition [^{fid}]: has no [^{fid}] reference in the body.",
            )
        )

    counts = Counter(defs)
    for fid in sorted(c for c, n in counts.items() if n > 1):
        diags.append(
            FootnoteDiagnostic(
                "warning",
                "import.footnote-duplicate",
                f"footnote definition [^{fid}]: appears {counts[fid]} times; only the "
                "first is used by Markdown renderers.",
            )
        )

    return diags
