# import-pure: no filesystem mutation
"""Footnote handling for the import pipeline — extraction + diagnosis.

This module is PURE: every function is a string/value transformation with no
filesystem access (the PAN018 marker above keys this contract). It exists to fix
and guard a proven shipped bug:

  Pandoc's GFM writer places ALL footnote definitions (`[^id]: …`) at the very
  TAIL of the document. The converter's bibliography stripper deletes from a
  `## Библиография`-type heading to the next heading; when that heading is the
  LAST one, it deletes to EOF — taking the footnote definitions with it and
  leaving orphaned `[^id]` references in the body (15 of 35 footnoted RU books).

The fix lives in two pieces this module provides:

  * `extract_footnote_defs` / `reattach_footnote_defs` — lift Pandoc's OWN
    emitted definitions out of the markdown BEFORE the tail-stripping passes run,
    then re-append the survivors at the document tail (Pandoc's original
    placement). We preserve Pandoc's definitions verbatim rather than
    reconstructing from the AST — they are already correct.

  * `analyze_footnotes` — a first-class diagnostic pass over the FINAL body: an
    `[^id]` reference with no matching `[^id]:` definition is FATAL (the
    orphaned-marker bug class); an unused definition or a duplicate id is a
    warning. The importer carries the fatal into the `WritePlan`, so the writer
    refuses — making it impossible to ship this bug class again.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

# A footnote DEFINITION line: `[^id]: body`. Anchored at line start (GFM defs are
# never indented at column 0 unless they ARE a definition). `id` is the run of
# non-`]` characters, mirroring `lib.docx_engine._FOOTNOTE_LINE`.
_DEF_LINE_RE = re.compile(r"^\[\^([^\]]+)\]:\s?(.*)$")

# A footnote REFERENCE marker anywhere inline: `[^id]` NOT followed by `:`
# (a `:` means it's the start of a definition, not a reference).
_REF_RE = re.compile(r"\[\^([^\]]+)\](?!:)")


@dataclass(frozen=True)
class FootnoteDef:
    """One extracted footnote definition, verbatim.

    `id` is the footnote id; `text` is the full definition block exactly as
    Pandoc emitted it — the `[^id]: …` line plus any indented continuation lines
    — with no trailing newline. Re-emitting `text` reproduces Pandoc's output.
    """

    id: str
    text: str


@dataclass(frozen=True)
class FootnoteDiagnostic:
    """A footnote finding: severity + stable code + human message.

    Deliberately a plain value (not `writeplan.Diagnostic`) so this module stays
    free of any import-pipeline coupling — the importer maps these onto
    `writeplan.Diagnostic`s when it folds them into the plan. `severity` is a
    `Literal` so callers building `writeplan.Diagnostic`s carry the precise type
    through with no cast.
    """

    severity: Literal["fatal", "warning"]
    code: str
    message: str


def _is_continuation(line: str) -> bool:
    """True if `line` continues the preceding footnote definition.

    GFM continuation lines are blank or indented (Pandoc emits 4-space
    indentation). A blank line alone is ambiguous, so the caller only treats a
    blank as a continuation when an indented line follows it; this predicate
    answers the indented-line case.
    """
    return bool(line) and line[:1] in (" ", "\t")


def extract_footnote_defs(md: str) -> tuple[str, list[FootnoteDef]]:
    """Lift every footnote definition block out of `md`.

    Returns `(body_without_defs, defs)` where `defs` preserves source order and
    each `FootnoteDef.text` is the verbatim def block (definition line plus any
    indented/blank-then-indented continuation lines). The returned body has the
    def blocks removed; `[^id]` references in the body are untouched (they are
    inline and must survive).

    This is the EXTRACT half of the fix — run it before the tail/bibliography
    stripping so Pandoc's correct definitions cannot be deleted with the
    bibliography section.
    """
    lines = md.splitlines()
    kept: list[str] = []
    defs: list[FootnoteDef] = []
    i = 0
    n = len(lines)
    while i < n:
        m = _DEF_LINE_RE.match(lines[i])
        if not m:
            kept.append(lines[i])
            i += 1
            continue
        # Start of a definition block. Consume the def line plus any continuation
        # lines: indented lines, and blank lines that are themselves followed by
        # an indented (continuation) line. A blank line followed by a
        # non-indented line ends the block (it is just paragraph spacing).
        block = [lines[i]]
        j = i + 1
        while j < n:
            if _is_continuation(lines[j]):
                block.append(lines[j])
                j += 1
                continue
            if lines[j].strip() == "":
                # Look past the blank line: only a following indented line keeps
                # the block open.
                k = j + 1
                while k < n and lines[k].strip() == "":
                    k += 1
                if k < n and _is_continuation(lines[k]):
                    block.extend(lines[j:k])
                    j = k
                    continue
            break
        defs.append(FootnoteDef(id=m.group(1), text="\n".join(block)))
        i = j
    return "\n".join(kept), defs


def reattach_footnote_defs(md: str, defs: list[FootnoteDef]) -> str:
    """Append `defs` to the END of `md` (Pandoc's original placement).

    A blank line separates the body from the definition block and each
    definition from the next, matching Pandoc's GFM output. Returns `md`
    unchanged when there are no definitions. This is the RE-APPEND half of the
    fix — run it after all stripping so the definitions survive."""
    if not defs:
        return md
    body = md.rstrip("\n")
    block = "\n\n".join(d.text for d in defs)
    if not body:
        return block + "\n"
    return f"{body}\n\n{block}\n"


def reference_ids(body: str) -> list[str]:
    """Every `[^id]` reference id in `body`, in order, with repeats."""
    return _REF_RE.findall(body)


def definition_ids(body: str) -> list[str]:
    """Every `[^id]:` definition id in `body`, in order, with repeats."""
    return [m.group(1) for m in (_DEF_LINE_RE.match(ln) for ln in body.splitlines()) if m]


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

    counts: dict[str, int] = {}
    for fid in defs:
        counts[fid] = counts.get(fid, 0) + 1
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
