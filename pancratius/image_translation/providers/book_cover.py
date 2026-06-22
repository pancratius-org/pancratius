"""Book-cover provider for the generic image translation engine.

This module owns all book-specific behavior: external cover queue paths, title
pins from en.md/seed/QUEUE, the Pancratius author override, and book-cover
output paths.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from pancratius.image_translation.models import (
    ExactText,
    ExpectedText,
    ImageTranslationJob,
    RoleSelector,
    TextOverride,
    TextRole,
)
from pancratius.image_translation.providers import ProviderJob
from pancratius.paths import CONTENT_ROOT

AUTHOR_RU = "Сергей Панкратиус"
AUTHOR_EN = "Sergei Pancratius"

DEFAULT_COVERS_DIR = Path.home() / "projects/misc/pancratius-misc/cover-queue"
DEFAULT_QUEUE_MD = DEFAULT_COVERS_DIR / "QUEUE.md"
DEFAULT_BOOKS_ROOT = CONTENT_ROOT / "books"
DEFAULT_SEED_PATH = DEFAULT_COVERS_DIR / "seed.json"


class TitleSource(str):
    """String constants for book title-pin provenance."""

    EN_MD = "en.md"
    SEED = "seed"
    QUEUE = "queue"
    MODEL = "model"


@dataclass(frozen=True, slots=True)
class TitlePin:
    """An authoritative English catalogue title and where it came from."""

    wording: str
    source: str


type ResolvedPin = TitlePin | None


@dataclass(frozen=True, slots=True)
class ResolvedTitle:
    """The book-provider decision for the primary image text."""

    to_render: str
    authoritative_wording: str
    source: str

    @property
    def is_pinned(self) -> bool:
        return bool(self.to_render)


UNRESOLVED_TITLE = ResolvedTitle(to_render="", authoritative_wording="", source=TitleSource.MODEL)


@dataclass(frozen=True, slots=True)
class SeedMap:
    """Human-curated book cover pins and exact text overrides."""

    titles: dict[str, str]
    overrides: dict[str, str]


def load_seed(seed_path: Path) -> SeedMap:
    """Return the seed map from `seed_path`, or an empty map when absent."""
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
        "overrides": "{ru_string: en_string} — exact source-text image translation overrides.",
    },
    "titles": {},
    "overrides": {},
}


def init_seed(seed_path: Path) -> None:
    """Write an empty seed template to `seed_path` if it does not exist."""
    if not seed_path.exists():
        seed_path.write_text(
            json.dumps(_SEED_TEMPLATE, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


_QUEUE_CELL_CAP = 39


def parse_queue_titles(queue_md: Path) -> tuple[dict[str, str], list[str]]:
    """Return ({book-XX: complete EN title}, [book-XX with clipped titles])."""
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
        if not num_raw.isdigit() or not en_title:
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


_TITLE_SUBTITLE_SEP = ":"


def _displayed_form(wording: str) -> str:
    head, _sep, _subtitle = wording.partition(_TITLE_SUBTITLE_SEP)
    return head.strip()


def normalise_book_key(raw: str) -> str:
    """Return canonical `book-NN` from `50`, `book-50`, or `book:50`."""
    value = raw.removeprefix("book:")
    if re.fullmatch(r"\d+", value):
        return f"book-{int(value):02d}"
    if re.fullmatch(r"book-\d+", value):
        return f"book-{int(value.split('-')[1]):02d}"
    raise ValueError(f"unrecognised book key {raw!r} (use 'book-50' or '50')")


def resolve_pin(
    book_key: str,
    *,
    books_root: Path,
    queue_titles: dict[str, str],
    seed: SeedMap,
) -> ResolvedPin:
    """Find the authoritative English title wording for a book, or None."""
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


def plan_title(pin: ResolvedPin) -> ResolvedTitle:
    """Compute the provider's primary-text title plan from a resolved pin."""
    if pin is None:
        return UNRESOLVED_TITLE
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
    return plan_title(
        resolve_pin(book_key, books_root=books_root, queue_titles=queue_titles, seed=seed)
    )


@dataclass(frozen=True, slots=True)
class BookImageTextPlan:
    """Book-provider visible text contract and source-keyed overrides."""

    expected_text: tuple[ExpectedText, ...]
    overrides: tuple[TextOverride, ...]


def text_plan_for_book(title: ResolvedTitle, seed: SeedMap) -> BookImageTextPlan:
    """Provider-built image text plan for a book cover."""
    expected: list[ExpectedText] = [
        ExpectedText((ExactText(AUTHOR_RU),), AUTHOR_EN, provenance="book-author"),
    ]
    overrides = [
        TextOverride(ExactText(source), target, provenance="seed-override")
        for source, target in seed.overrides.items()
    ]
    if title.is_pinned:
        expected.append(
            ExpectedText(
                (RoleSelector(TextRole.PRIMARY),),
                title.to_render,
                provenance=f"book-title:{title.source}",
            )
        )
    return BookImageTextPlan(expected_text=tuple(expected), overrides=tuple(overrides))


def _source_path(covers_dir: Path, book_key: str) -> Path:
    for ext in (".ru.png", ".ru.jpg"):
        p = covers_dir / f"{book_key}{ext}"
        if p.exists():
            return p
    return covers_dir / f"{book_key}.ru.png"


def discover_books(covers_dir: Path) -> list[str]:
    """Every book-XX with a source cover present, sorted by number."""
    keys: set[str] = set()
    for p in covers_dir.glob("book-*.ru.*"):
        m = re.match(r"(book-\d+)\.ru\.", p.name)
        if m:
            keys.add(m.group(1))
    return sorted(keys, key=lambda k: int(k.split("-")[1]))


@dataclass(frozen=True, slots=True)
class BookCoverProvider:
    """Build generic image-translation jobs for book covers."""

    output_dir: Path
    covers_dir: Path = DEFAULT_COVERS_DIR
    queue_md: Path = DEFAULT_QUEUE_MD
    books_root: Path = DEFAULT_BOOKS_ROOT
    seed_path: Path = DEFAULT_SEED_PATH

    def title_for(self, book_key: str) -> ResolvedTitle:
        queue_titles: dict[str, str] = {}
        if self.queue_md.exists():
            queue_titles, _ = parse_queue_titles(self.queue_md)
        return resolve_title(
            book_key,
            books_root=self.books_root,
            queue_titles=queue_titles,
            seed=load_seed(self.seed_path),
        )

    def spec(self, raw_key: str) -> ProviderJob:
        book_key = normalise_book_key(raw_key)
        seed = load_seed(self.seed_path)
        title = self.title_for(book_key)
        text_plan = text_plan_for_book(title, seed)
        job = ImageTranslationJob(
            key=book_key,
            source_image=_source_path(self.covers_dir, book_key),
            target_image=self.output_dir / f"{book_key}.en.png",
            raw_image=self.output_dir / f"{book_key}.raw.png",
            expected_text=text_plan.expected_text,
            overrides=text_plan.overrides,
            context="book cover",
            allow_embedded_text_caveat=True,
            metadata={
                "kind": "book-cover",
                "title_source": title.source,
                "title_target": title.to_render,
                "title_wording": title.authoritative_wording,
            },
        )
        return ProviderJob(job=job, label=book_key)
