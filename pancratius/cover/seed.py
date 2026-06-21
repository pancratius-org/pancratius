"""Seed map: parse QUEUE.md titles and load seed.json overrides/pins.

The seed carries two human-owned layers:
  - ``titles``: manual pins {book-XX: EN title}, an escape hatch when en.md is
    missing and QUEUE is clipped. en.md always wins over this.
  - ``overrides``: {ru_string: en_string}, the whole string-level review
    mechanism. Add one entry and re-run that cover to fix any rendered string.

Title resolution priority:
  1. en.md frontmatter (authoritative, untruncated)
  2. manual seed pin
  3. complete QUEUE.md title (clipped ones are excluded — they'd render half-titles)
  4. none: model translates the displayed title from the cover image
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from pancratius.cover.models import (
    AUTHOR_EN,
    AUTHOR_RU,
    CoverElement,
    ElementRole,
    ResolvedElement,
    ResolvedPin,
    ResolvedTitle,
    TitlePin,
    TitleSource,
)

# QUEUE.md clips its EN-title cell at ~40 chars. Any title >= this length is
# suspect (cut mid-phrase) — we skip it rather than pin a half-title.
_QUEUE_CELL_CAP = 39


@dataclass(frozen=True, slots=True)
class SeedMap:
    """Human-curated overrides and manual title pins loaded from seed.json."""

    titles: dict[str, str]  # {book-XX: EN title}
    overrides: dict[str, str]  # {ru_string: en_string}


def load_seed(seed_path: Path) -> SeedMap:
    """Return the SeedMap from seed_path, or an empty SeedMap if the file is absent.

    Pure read — no write side-effect. Call init_seed to create the template file.
    """
    if not seed_path.exists():
        return SeedMap(titles={}, overrides={})
    raw = json.loads(seed_path.read_text(encoding="utf-8"))
    return SeedMap(
        titles=dict(raw.get("titles") or {}),
        overrides=dict(raw.get("overrides") or {}),
    )


_SEED_TEMPLATE: dict[str, object] = {
    "_doc": {
        "titles": "Manual title pins {book-XX: EN title}. en.md always wins over this.",
        "overrides": (
            "{ru_string: en_string} — add one entry to fix any rendered string, "
            "then re-run that cover."
        ),
    },
    "titles": {},
    "overrides": {},
}


def init_seed(seed_path: Path) -> None:
    """Write the empty seed template to seed_path if the file does not yet exist."""
    if not seed_path.exists():
        seed_path.write_text(
            json.dumps(_SEED_TEMPLATE, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def parse_queue_titles(queue_md: Path) -> tuple[dict[str, str], list[str]]:
    """Return ({book-XX: complete EN title}, [book-XX with clipped titles]).

    Only titles short enough to be certainly complete are returned; clipped ones
    are reported for human follow-up.
    """
    titles: dict[str, str] = {}
    clipped: list[str] = []
    for line in queue_md.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 4:
            continue
        num_raw, _status, _ru, en_title = cells[0], cells[1], cells[2], cells[3]
        if not num_raw.isdigit():
            continue
        if not en_title:
            continue
        key = f"book-{int(num_raw):02d}"
        if len(en_title) >= _QUEUE_CELL_CAP:
            clipped.append(key)
        else:
            titles[key] = en_title
    return titles, clipped


def _book_dir(books_root: Path, num: int) -> Path | None:
    prefix = f"{num:02d}-"
    if not books_root.exists():
        return None
    for d in books_root.iterdir():
        if d.is_dir() and d.name.startswith(prefix):
            return d
    return None


def _unquote_yaml_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == "'" and value[-1] == "'":
        return value[1:-1].replace("''", "'")
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return value


def _enmd_title(books_root: Path, num: int) -> str | None:
    """EN title from src/content/books/<NN>-*/en.md frontmatter, or None."""
    d = _book_dir(books_root, num)
    if d is None:
        return None
    en = d / "en.md"
    if not en.exists():
        return None
    in_frontmatter = False
    for line in en.read_text(encoding="utf-8").splitlines():
        if line.strip() == "---":
            if not in_frontmatter:
                in_frontmatter = True
                continue
            break
        if in_frontmatter and line.startswith("title:"):
            return _unquote_yaml_scalar(line[len("title:"):]) or None
    return None


# Separator between a catalogue title and its subtitle. The portion BEFORE the
# first colon is the short title that physically appears on the cover (the rest
# is subtitle the cover does not display). Confirmed across the corpus: catalogue
# titles are "<Short>: <subtitle>" or have no colon at all (the whole thing is the
# short title). A bare period/comma is NOT a separator — they occur mid-title
# (e.g. "Now You See Me. Too.", "The Book of Genesis, Alive").
_TITLE_SUBTITLE_SEP = ":"


def resolve_pin(
    book_key: str,
    *,
    books_root: Path,
    queue_titles: dict[str, str],
    seed: SeedMap,
) -> ResolvedPin:
    """Find the authoritative English title wording for a book, or None.

    Priority (highest first): en.md > seed manual pin > complete QUEUE title.
    Returns None when no pin is available (the model translates the displayed
    title itself).
    """
    m = re.search(r"\d+", book_key)
    if m is None:
        raise ValueError(f"book_key has no digits: {book_key!r}")
    num = int(m.group())
    title = _enmd_title(books_root, num)
    if title:
        return TitlePin(wording=title, source=TitleSource.EN_MD)
    if book_key in seed.titles:
        return TitlePin(wording=seed.titles[book_key], source=TitleSource.SEED)
    if book_key in queue_titles:
        return TitlePin(wording=queue_titles[book_key], source=TitleSource.QUEUE)
    return None


def _displayed_form(wording: str) -> str:
    """The cover-displayed short title: the segment before the title/subtitle colon."""
    head, _sep, _subtitle = wording.partition(_TITLE_SUBTITLE_SEP)
    return head.strip()


def plan_title(pin: ResolvedPin) -> ResolvedTitle:
    """Compute the single title-to-render from a resolved pin.

    The cover physically shows the SHORT title, never the full catalogue title.
    When a pin exists we derive that short form (the part before the colon) as the
    authoritative English wording to render — so the prompt pins exactly one string
    and never has to fight a full-vs-displayed contradiction. With no pin the model
    translates the displayed title itself (`to_render` is empty).
    """
    if pin is None:
        return ResolvedTitle(to_render="", authoritative_wording="", source=TitleSource.MODEL)
    return ResolvedTitle(
        to_render=_displayed_form(pin.wording),
        authoritative_wording=pin.wording,
        source=pin.source,
    )


def resolve_title(
    book_key: str,
    *,
    books_root: Path,
    queue_titles: dict[str, str],
    seed: SeedMap,
) -> ResolvedTitle:
    """Resolve the title-to-render for a cover end to end (pin lookup + plan)."""
    return plan_title(
        resolve_pin(book_key, books_root=books_root, queue_titles=queue_titles, seed=seed)
    )


def _resolve_element_english(
    element: CoverElement,
    *,
    title: ResolvedTitle,
    overrides: dict[str, str],
) -> str:
    """The one authoritative English for one element, by precedence.

    1. An ``overrides`` entry keyed on the exact Russian wins for any element —
       it is the operator's string-level escape hatch.
    2. The title pin wins for the TITLE element (``title.to_render`` is the
       short cover form, e.g. "Mammon"); only when actually pinned.
    3. The fixed author string wins for the AUTHOR element.
    4. Otherwise the recon model's own English for the element.
    """
    if (override := overrides.get(element.russian)) is not None:
        return override
    if element.role is ElementRole.TITLE and title.is_pinned:
        return title.to_render
    if element.role is ElementRole.AUTHOR:
        return AUTHOR_EN
    return element.english


def resolve_elements(
    elements: Iterable[CoverElement],
    *,
    title: ResolvedTitle,
    overrides: dict[str, str],
) -> tuple[ResolvedElement, ...]:
    """Resolve every recon element to one authoritative English string.

    Each element ends with a single English wording (see ``_resolve_element_english``);
    the generation prompt renders exactly these, so the model never finds-and-translates
    on its own. Elements whose Russian is blank are dropped (nothing to replace).
    """
    resolved: list[ResolvedElement] = []
    for element in elements:
        if not element.russian.strip():
            continue
        resolved.append(
            ResolvedElement(
                role=element.role,
                russian=element.russian,
                english=_resolve_element_english(element, title=title, overrides=overrides),
                art_baked=element.art_baked,
            )
        )
    return tuple(resolved)


def author_only_elements(*, art_baked: bool = False) -> tuple[ResolvedElement, ...]:
    """Fallback replacement map when recon yielded no usable elements.

    Recon parse failure must still let the author be pinned, so generation keeps a
    minimal authoritative instruction rather than reverting to translate-it-yourself.
    The Russian author string is the corpus invariant ``AUTHOR_RU``.
    """
    return (
        ResolvedElement(
            role=ElementRole.AUTHOR,
            russian=AUTHOR_RU,
            english=AUTHOR_EN,
            art_baked=art_baked,
        ),
    )
