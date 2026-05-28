"""Verify book verse-block decisions are faithful to the DOCX source (I4).

Poems have ``poetry_stanzas.py``; books had NOTHING — a blind spot that let a
verse signature-drift bug nearly ship. This audit is the permanent DOCX-source
oracle for BOOK verse, the executable spec for the IR detection in
``pancratius/ir/normalize.py`` (``verse_blocks`` / ``_run_kind`` /
``_is_lineated_line``).

It reads each book's DOCX structure INDEPENDENTLY of the importer (pandoc's
``docx+empty_paragraphs`` AST for paragraphs, empties, and hard ``LineBreak``s,
plus the OOXML ``w:jc`` right-alignment for signature/epigraph), derives the
EXPECTED verse / signature / epigraph structure under a clear conservative rule,
then compares it to the CONVERTED Markdown — flagging BOTH:

  * OVER-detection — the IR wrapped a verse-block the source rule does not call a
    confident verse run (an isolated short line, a `Speaker:` / `Speaker (qual):`
    label line, a prose-length line, one prose sentence after a label).
  * UNDER-detection — the source has a confident verse run the IR left as prose.

It ALSO asserts every converted signature/epigraph is DRAWN FROM the right-aligned
(``w:jc``) source — a block built from non-right-aligned text is the symptom of the
C1 ``w:jc``-realignment drift — so it doubles as the C1 / I2 regression guard.

The verse rule (the SPEC — mirrored by the IR, unit-tested in
``tests/audit/test_book_verse.py`` and ``tests/test_ir_verse.py``):

  * A *verse run* is >=2 consecutive SHORT lineated display-lines whose lineation
    comes from the SOURCE — a hard ``LineBreak`` (``<w:br/>``) inside one
    paragraph, a run after a heading, or a run of short standalone paragraphs
    separated only by empty paragraphs (stanza breaks). Each line must be under
    ``SHORT_LINE_MAX`` chars.
  * A run of BARE standalone single-line paragraphs is a CONFIDENT verse run only
    when it carries a stanza-break empty paragraph (the source separates its lines
    with a blank Word paragraph) AND has >=3 lines — paragraph boundaries alone
    don't separate a short couplet from two prose sentences, so a 2-line run with
    no hard break (a couplet that is just as likely two prose sentences) is left to
    prose. A hard ``<w:br/>`` line is a CONFIDENT signal on its own and counts at
    >=2. (These two floors mirror the IR's ``_run_kind``: the empty-paragraph path
    requires the >=3 weak-signal floor; the hard-break path counts at >=2.)
  * NOT a verse line: an explicit SPEAKER/SOURCE turn (`Speaker:` /
    `Speaker: content`); a LONG (prose-length) line; a numbered Q/A heading; a
    list item; an image/table/link line.

Tiering: this is a ``heuristic`` (agent-tier, non-blocking) audit, like
``poetry_stanzas`` — it runs in ``npm run audit:agent`` and never gates the PR
core or a deploy. Rationale: the source rule is conservative and matches the IR
today (over/under-detection both ~0), but book verse is partly an editorial call
(a litany boundary the lead signs off on), so it is GUIDANCE until the rule has
proven a hard contract and earned a both-polarity fixture — the same promotion
path ``content-model.md`` records for ``poetry_stanzas``.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("PANCRATIUS_AUDIT_ROOT") or Path(__file__).resolve().parents[1])
CONTENT = ROOT / "src" / "content" / "books"

# The short-line length cap — a display line longer than this is "prose-length"
# and is NOT a verse line. Mirrors ``ir.normalize.VERSE_SHORT_LINE_MAX``; the two
# are kept in sync deliberately (the audit is the spec the IR implements).
SHORT_LINE_MAX = 120

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

_NUMBERED_QUESTION_RE = re.compile(r"^\d{1,3}[.)]\s+\S.*[?？]\s*$")


def _speaker_turn_re() -> re.Pattern[str]:
    """A SPEAKER-led colon line (a dialogue/source TURN, never verse): a known
    dialogue prefix + colon, OR `<Name> (qualifier):` + content. Built from the IR
    dialogue prefixes (`ir.normalize._DIALOGUE_PREFIXES`, the dialogue SoT) so the
    audit's speaker set tracks the IR's. Distinct from a mid-sentence colon in a
    verse line (`Ты спросил: кто они?`), which is NOT rejected."""
    from pancratius.ir.normalize import _DIALOGUE_PREFIXES

    prefixes = sorted(_DIALOGUE_PREFIXES, key=lambda p: -len(p))
    inner = "|".join(re.escape(p) for p in prefixes)
    return re.compile(
        rf"^\**\s*(?:(?:{inner})|[A-ZА-ЯЁ][\wА-Яа-яЁё.\- ]{{0,40}}\s*\([^)]{{1,40}}\))"
        rf"\s*:(?:\s|\*|$)"
    )


SPEAKER_TURN_RE = _speaker_turn_re()


# ---------------------------------------------------------------------------
# the verse rule, as testable pure helpers (the SPEC)
# ---------------------------------------------------------------------------


def is_label_line(line: str) -> bool:
    """Backward-compatible helper: explicit speaker/source labels are not verse."""
    return is_speaker_turn(line)


def is_speaker_turn(line: str) -> bool:
    """A `Speaker: content` / `Name (qual): content` dialogue/source turn — not
    verse, in any context (unlike a verse line's mid-sentence colon)."""
    return bool(SPEAKER_TURN_RE.match(line.strip()))


def is_verse_line(line: str) -> bool:
    """True for a single short source line that reads as a verse line, not prose /
    a label / a speaker turn / a list item / a Q&A heading / an image-or-table line."""
    s = re.sub(r"\s+", " ", line).strip()
    if not s or len(s) > SHORT_LINE_MAX:
        return False
    if s.startswith(("!", "<", "|", ">", "[]")):
        return False
    if re.match(r"^[-*+]\s+", s) or re.match(r"^\d+[.)]\s+", s):
        return False
    if s in {"***", "* * *"}:  # a thematic break, not a verse line
        return False
    if _NUMBERED_QUESTION_RE.match(s):
        return False
    if is_speaker_turn(s):
        return False
    if "http://" in s or "https://" in s:
        return False
    return True


# A non-``Para`` body block (heading / table / list / image / blockquote) — a
# structural boundary that ENDS any in-progress verse run. Carried as a unit so
# the grouping sees document order; it is never a verse line.
STRUCTURAL_BREAK = "\x00BREAK"
HEADING_BREAK = "\x00HEADING"


def group_expected_runs(units: list[tuple[str, bool]]) -> list[list[str]]:
    """Group source paragraph UNITS into the verse runs the rule expects.

    ``units`` is the document's body blocks in order, each a ``(text, is_empty)``
    pair: a NON-empty paragraph unit holds one source paragraph's collapsed display
    lines joined by ``\\n`` (its hard ``<w:br/>`` lineation); an empty unit is a
    Word empty paragraph (a stanza break); a ``STRUCTURAL_BREAK`` text marks a
    non-paragraph block (table/list/…); a ``HEADING_BREAK`` marks a heading.

    A run accumulates consecutive lineated paragraphs (every display line is a
    verse line) with empty paragraphs allowed BETWEEN them as stanza breaks; a
    non-verse line (label / prose / list / heading) or a structural block ends the
    run. The run is KEPT as expected-verse only when it carries a CONFIDENT
    source-lineation signal (matching the IR's conservative ``_run_kind`` — a bare
    run of short standalone paragraphs is NOT folded without one, because paragraph
    boundaries alone don't tell a couplet from two prose sentences):

      * a heading immediately before the run and 2–32 short lines within the
        converter's heading-run length thresholds; OR
      * a hard ``<w:br/>`` (>=1 multi-line unit) and the run has >=2 lines — the
        strong in-paragraph source-lineation signal; OR
      * a stanza-break empty paragraph inside the run AND >=3 lines — the source
        separated its lines with a blank Word paragraph (the weak-signal floor).
    """
    runs: list[list[str]] = []
    cur_lines: list[str] = []
    cur_has_hardbreak = False
    cur_has_empty = False
    cur_after_heading = False
    pending_heading = False

    def flush() -> None:
        nonlocal cur_lines, cur_has_hardbreak, cur_has_empty, cur_after_heading
        if cur_lines:
            lengths = [len(line) for line in cur_lines]
            avg = sum(lengths) / len(lengths)
            max_len = max(lengths)
            confident = (
                cur_after_heading
                and 2 <= len(cur_lines) <= 32
                and avg <= 95
                and max_len <= 150
            ) or (cur_has_hardbreak and len(cur_lines) >= 2) or (
                cur_has_empty and len(cur_lines) >= 3 and avg <= 120
            )
            if confident:
                runs.append(cur_lines)
        cur_lines = []
        cur_has_hardbreak = False
        cur_has_empty = False
        cur_after_heading = False

    for text, is_empty in units:
        if text == HEADING_BREAK:
            flush()
            pending_heading = True
            continue
        if text == STRUCTURAL_BREAK:
            flush()
            pending_heading = False
            continue
        if is_empty:
            # An empty paragraph is a stanza break: it does NOT end an in-progress
            # verse run, it marks an internal break (recorded for the >=2+empty
            # confidence rule). A trailing empty after a run still flushes via the
            # next non-verse unit or end of document.
            if cur_lines:
                cur_has_empty = True
            continue
        display = [ln for ln in text.split("\n") if ln.strip()]
        if display and all(is_verse_line(ln) for ln in display):
            if not cur_lines and pending_heading:
                cur_after_heading = True
            cur_lines.extend(re.sub(r"\s+", " ", ln).strip() for ln in display)
            if len(display) > 1:
                cur_has_hardbreak = True
            pending_heading = False
        else:
            flush()
            pending_heading = False
    flush()
    return runs


# ---------------------------------------------------------------------------
# DOCX source read (independent of the importer)
# ---------------------------------------------------------------------------


def _inlines_to_text(inlines: list[dict[str, Any]]) -> str:
    """Flatten a Pandoc inline list, hard ``LineBreak`` -> ``\\n``, soft break ->
    space (soft breaks are prose wrapping, not authored lineation — the C2 rule)."""
    out: list[str] = []
    for item in inlines:
        typ = item.get("t")
        val: Any = item.get("c")
        if typ == "Str":
            out.append(str(val))
        elif typ == "Space" or typ == "SoftBreak":
            out.append(" ")
        elif typ == "LineBreak":
            out.append("\n")
        elif typ in {"Strong", "Emph", "Underline", "SmallCaps", "Strikeout"}:
            out.append(_inlines_to_text(val or []))
        elif typ == "Quoted":
            out.append(_inlines_to_text(val[1]))
        elif typ == "Code":
            out.append(str(val[1]))
        elif typ in {"Link", "Span"}:
            out.append(_inlines_to_text(val[1]))
        elif typ == "Image":
            continue
        elif isinstance(val, list):
            out.append(_inlines_to_text(val))
    return "".join(out)


def _inline_kinds(inlines: list[dict[str, Any]]) -> set[str]:
    """The set of Pandoc inline tags anywhere in the tree (recursing containers) —
    used to tell a paragraph's break kind apart (``SoftBreak`` vs ``LineBreak``)."""
    kinds: set[str] = set()
    for item in inlines:
        typ = item.get("t")
        if typ:
            kinds.add(str(typ))
        val = item.get("c")
        if isinstance(val, list):
            if typ in {"Strong", "Emph", "Underline", "SmallCaps", "Strikeout"}:
                kinds |= _inline_kinds(val)
            elif typ in {"Quoted", "Link", "Span"} and len(val) >= 2 and isinstance(val[1], list):
                kinds |= _inline_kinds(val[1])
            elif typ not in {"Str", "Code", "Image"}:
                kinds |= _inline_kinds(val)
    return kinds


def _is_wrapped_prose(inlines: list[dict[str, Any]]) -> bool:
    """True when a paragraph's only in-run breaks are ``SoftBreak``s (prose
    wrapping, a literal ``\\r\\n`` in one ``<w:t>``) with NO hard ``LineBreak``.
    Such a paragraph is PROSE, never a verse line — its lineation was never
    authored. Mirrors ``ir.normalize._is_wrapped_prose`` (the C2 over-detection
    fix), so the audit's expected set agrees with the IR on wrapped prose."""
    kinds = _inline_kinds(inlines)
    return "SoftBreak" in kinds and "LineBreak" not in kinds


def source_units(docx: Path) -> list[tuple[str, bool]]:
    """The body paragraph units `(text, is_empty)` in document order.

    Only ``Para``/``Plain`` blocks become units; a heading/table/list/blockquote/
    image becomes a structural break (an empty-text NON-empty unit sentinel is not
    needed — a non-``Para`` simply yields nothing, ending any run because the next
    ``Para`` starts fresh after the gap)."""
    proc = subprocess.run(
        ["pandoc", "--from", "docx+empty_paragraphs", "--to", "json", str(docx)],
        capture_output=True, text=True, check=True,
    )
    blocks = json.loads(proc.stdout).get("blocks") or []
    units: list[tuple[str, bool]] = []
    for block in blocks:
        t = block.get("t")
        if t == "Header":
            units.append((HEADING_BREAK, False))
            continue
        if t not in {"Para", "Plain"}:
            # A structural block breaks any in-progress run: emit a sentinel that
            # group_expected_runs treats as a non-verse boundary.
            units.append((STRUCTURAL_BREAK, False))
            continue
        inlines = block.get("c") or []
        if not inlines:
            units.append(("", True))
            continue
        if _is_wrapped_prose(inlines):
            # Soft-break-wrapped prose is PROSE, not authored lineation (C2): treat
            # it as a structural boundary so it never enters a verse run — exactly
            # what the IR's `_para_lineated` does via `_is_wrapped_prose`.
            units.append((STRUCTURAL_BREAK, False))
            continue
        text = _inlines_to_text(inlines)
        units.append((text, False))
    return units


def expected_verse_runs(docx: Path) -> list[list[str]]:
    """The verse RUNS (each a list of normalized lines) the source rule expects."""
    return group_expected_runs(source_units(docx))


# ---------------------------------------------------------------------------
# converted-Markdown read (the actual IR output)
# ---------------------------------------------------------------------------

_VERSE_RE = re.compile(r'<div class="verse-block">\n(.*?)\n</div>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def _norm(line: str) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub("", line)).strip()


def actual_block_lines(md_body: str) -> set[str]:
    """The normalized lines the IR placed inside ``verse-block`` divs."""
    verse: set[str] = set()
    for m in _VERSE_RE.finditer(md_body):
        for raw in m.group(1).splitlines():
            line = _norm(raw)
            if line and line != "***":
                verse.add(line)
    return verse


def actual_verse_lines(md_body: str) -> set[str]:
    """All lines the IR placed inside a verse block."""
    return actual_block_lines(md_body)


_SIGNATURE_RE = re.compile(r'<p class="signature">\n(.*?)\n</p>', re.S)
_EPIGRAPH_RE = re.compile(r'<blockquote class="epigraph">\n(.*?)\n</blockquote>', re.S)
_WORD_RE = re.compile(r"\w+", re.UNICODE)


def actual_structural_blocks(md_body: str) -> list[tuple[str, str]]:
    """``(role, text)`` for each converted signature / epigraph block (tags
    stripped). These come ONLY from right-aligned (``w:jc``) source groups."""
    out: list[tuple[str, str]] = []
    out += [("signature", _norm(m)) for m in _SIGNATURE_RE.findall(md_body)]
    out += [("epigraph", _norm(m.replace("\n", " ")))
            for m in _EPIGRAPH_RE.findall(md_body)]
    return out


def source_right_aligned_words(docx: Path) -> set[str]:
    """The reading-word set of all RIGHT-aligned source paragraphs (``w:jc`` =
    ``right``/``end``). A converted signature/epigraph must be drawn from THIS set
    — a block whose words are NOT mostly right-aligned source is a spurious
    signature/epigraph, the symptom of the C1 ``w:jc``-realignment drift (a
    positional zip mis-assigning alignment). Reuses the importer's ``read_w_jc``."""
    from pancratius import docx_adapter

    words: set[str] = set()
    for rec in docx_adapter.read_w_jc(docx):
        if rec.align in {"right", "end"}:
            words.update(_WORD_RE.findall(rec.text.lower()))
    return words


def prose_lines(md_body: str) -> set[str]:
    """The set of normalized lines OUTSIDE any verse block — the body's
    prose (and heading/label) lines. Used to confirm an UNDER-detection: a source
    verse run is only "missed" if its lines survived in the body AS PROSE (so a
    scrubbed/demoted/canonicalized source line — gone from the body entirely — is
    not falsely reported as a missed verse run)."""
    body = _VERSE_RE.sub("", md_body)
    return {_norm(ln) for ln in body.splitlines() if _norm(ln)}


def _md_body(md_text: str) -> str:
    return md_text.split("---", 2)[2] if md_text.count("---") >= 2 else md_text


# ---------------------------------------------------------------------------
# source-mapping + import (mirrors poetry_stanzas)
# ---------------------------------------------------------------------------


def _book_dir(number: int) -> Path | None:
    matches = sorted(CONTENT.glob(f"{number:02d}-*"))
    return matches[0] if matches else None


def _source_parts(number: int) -> list[Path]:
    book_dir = _book_dir(number)
    if book_dir is None:
        return []
    return sorted(p for p in book_dir.glob("ru-part*.docx") if not p.name.startswith("~$"))


def source_docx(number: int) -> Path:
    """The committed RU book DOCX for ``number``.

    Multi-part books keep ``ru-part*.docx`` beside the merged ``ru.docx`` and are
    skipped by the caller: the source oracle needs one authored DOCX, not a
    generated merge.
    """
    book_dir = _book_dir(number)
    if book_dir is None:
        raise FileNotFoundError(f"book content folder not found for #{number}")
    single = book_dir / "ru.docx"
    if single.is_file():
        return single
    parts = _source_parts(number)
    if len(parts) == 1:
        return parts[0]
    raise FileNotFoundError(f"committed RU book DOCX not found for #{number}")


def _is_multipart(number: int) -> bool:
    return len(_source_parts(number)) > 1


def _committed_book_meta() -> list[tuple[int, str, str]]:
    """``(number, title, slug)`` for every committed book, sorted by number — the
    same inputs the importer needs to reproduce a book's body deterministically."""
    meta: list[tuple[int, str, str]] = []
    for md in sorted(CONTENT.glob("*/ru.md")):
        text = md.read_text(encoding="utf-8")
        m = re.search(r"^number:\s*(\d+)\s*$", text, re.M)
        if not m:
            continue
        number = int(m.group(1))
        tm = re.search(r"^title:\s*(.+?)\s*$", text, re.M)
        title = tm.group(1).strip().strip("'\"") if tm else ""
        sm = re.search(r"^slug:\s*(.+?)\s*$", text, re.M)
        slug = sm.group(1).strip().strip("'\"") if sm else md.parent.name
        meta.append((number, title, slug))
    return sorted(meta)


def _compare(number: int, docx: Path, md_body: str) -> list[str]:
    """Compare the source-rule verse structure to the converted Markdown.

    Two robust, independent directions:

      * OVER-detection — every line the IR actually wrapped in a verse-block must
        ITSELF be a valid verse line (`is_verse_line`): not a `Speaker:` label, not
        a prose-length line, not a numbered Q&A heading / list item. This needs no
        source re-derivation (the line text is the converted output), so it is the
        primary, drift-proof check — it is exactly the spec's over-detection.

      * UNDER-detection — a confident source verse RUN whose lines ALL survived in
        the converted body AS PROSE (present, but none inside a verse-block) was
        left ungrouped. Guarding on "present as prose" excludes any source line the
        normalizer legitimately removed/changed before verse detection (rights
        scrub, TOC/bibliography lift, heading demotion, dialogue-label canon), so
        only a genuine missed grouping is reported.
    """
    out: list[str] = []

    actual = actual_block_lines(md_body)
    # OVER-detection: a verse-block line that is UNAMBIGUOUSLY not verse —
    # a SPEAKER TURN (`Панкратиус: …` / `Ответ от Творца (…): …`), a PROSE-LENGTH
    # line (> the short-line cap), or a numbered Q&A heading. A bare terminal-colon
    # phrase (`Спроси:` / `Молитва узнавания:`) is NOT flagged: it legitimately
    # OPENS a multi-line verse stanza (a hard-`<w:br/>` paragraph whose first line
    # ends in a colon) and is genuine verse there — flagging it would be a false
    # positive.
    def over_line(ln: str) -> bool:
        return (
            is_speaker_turn(ln)
            or len(ln) > SHORT_LINE_MAX
            or bool(_NUMBERED_QUESTION_RE.match(ln))
        )

    over = sorted(ln for ln in actual if over_line(ln))
    if over:
        out.append(
            f"book #{number:02d}: OVER-detection — {len(over)} line(s) wrapped as "
            f"verse that are not verse lines (label / prose-length / Q&A heading):"
        )
        out.extend(f"      + {ln[:90]}" for ln in over[:6])

    prose = prose_lines(md_body)
    missed: list[list[str]] = []
    for run in expected_verse_runs(docx):
        # A run is "missed" when any of its source lines survived as prose instead
        # of all of them reaching the verse block. This catches partial splits like
        # book #30 item 23, where the opener wrapped but the rest was left as prose.
        if all(ln in actual or ln in prose for ln in run) and any(ln in prose for ln in run):
            missed.append(run)
    if missed:
        n_lines = sum(len(r) for r in missed)
        out.append(
            f"book #{number:02d}: UNDER-detection — {len(missed)} confident source "
            f"verse run(s) ({n_lines} line(s)) left as prose:"
        )
        for run in missed[:4]:
            out.append(f"      - [{len(run)} lines] {run[0][:80]}")

    # C1/I2 guard: every converted signature/epigraph must be drawn from the
    # right-aligned (`w:jc`) source. A block whose words are mostly NOT from the
    # right-aligned source is a SPURIOUS signature/epigraph — the symptom of the C1
    # `w:jc`-realignment drift (a positional zip mis-assigning alignment, the bug
    # that silently moved a signature off its source paragraph). This direction is
    # robust (it needs no source-side classification), unlike a blunt count: not
    # every right-aligned group becomes a signature, so absence is not loss — but a
    # signature built from non-right-aligned text is unambiguous drift.
    right_words = source_right_aligned_words(docx)
    for role, text in actual_structural_blocks(md_body):
        words = set(_WORD_RE.findall(text.lower()))
        if words and len(words - right_words) / len(words) > 0.5:
            out.append(
                f"book #{number:02d}: SPURIOUS {role} — a {role} whose text is not "
                f"from the right-aligned source (the C1/I2 w:jc-realignment drift): "
                f"{text[:80]}"
            )
    return out


def _check_committed() -> int:
    failures: list[str] = []
    checked = 0
    for number, _title, slug in _committed_book_meta():
        if _is_multipart(number):
            continue
        try:
            docx = source_docx(number)
        except FileNotFoundError:
            continue
        md = (CONTENT / slug / "ru.md")
        if not md.is_file():
            continue
        failures.extend(_compare(number, docx, _md_body(md.read_text(encoding="utf-8"))))
        checked += 1
    return _report(checked, failures)


def _check_from_ir() -> int:
    """Import each book through the live importer (the typed-IR path) into a temp
    tree and run the oracle on that FRESH output — validates the IR conversion
    directly, not the (possibly hand-edited) committed Markdown."""
    import contextlib
    import io
    import tempfile

    from pancratius import import_docx

    failures: list[str] = []
    checked = 0
    for number, title, slug in _committed_book_meta():
        if _is_multipart(number):
            continue
        try:
            docx = source_docx(number)
        except FileNotFoundError:
            continue
        with tempfile.TemporaryDirectory(prefix="book-verse-ir-") as td:
            content_root = Path(td) / "src" / "content"
            request = import_docx.ImportRequest(
                docx=docx,
                kind="book",
                lang="ru",
                number=number,
                slug=slug,
                title=title,
                out_content=content_root,
            )
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                report = import_docx.import_work(request)
            if report.refused:
                failures.append(f"book #{number:02d}: IR import refused")
                continue
            work_key = slug if re.match(r"^\d{1,4}-", slug) else f"{number:02d}-{slug}"
            md_body = _md_body((content_root / "books" / work_key / "ru.md").read_text(encoding="utf-8"))
        failures.extend(_compare(number, docx, md_body))
        checked += 1
    return _report(checked, failures, mode="IR import path")


def _report(checked: int, failures: list[str], mode: str = "committed content") -> int:
    if failures:
        print(f"FAIL: book verse-block source-fidelity mismatches ({mode})")
        for failure in failures[:60]:
            print(" ", failure)
        if len(failures) > 60:
            print(f"  ... {len(failures) - 60} more")
        return 1
    print(f"checked {checked} books via {mode}; verse-block decisions match the DOCX source rule")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--from-ir",
        action="store_true",
        help="Import each book through the live importer (IR path) into a temp tree "
        "and run the source-verse oracle on that fresh output, instead of reading "
        "the committed Markdown.",
    )
    args = ap.parse_args(argv)
    return _check_from_ir() if args.from_ir else _check_committed()


if __name__ == "__main__":
    sys.exit(main())
