"""Project cover provider for the generic image translation engine."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pancratius.content_catalog import split_frontmatter
from pancratius.paths import CONTENT_ROOT
from pancratius.selectors import (
    ProjectResourceSelector,
    ProjectSelector,
    ProjectSubpageSelector,
)
from pancratius.translation.image.models import (
    ExpectedText,
    ImageTranslationJob,
    ImageTranslationResult,
    NormalizedText,
    RoleSelector,
    TextOverride,
    TextRole,
)
from pancratius.translation.image.providers import FrontmatterUpdate, ProviderJob

_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)

COMMON_PROJECT_OVERRIDES: tuple[tuple[str, str], ...] = (
    ("Сергей Панкратиус", "Sergei Pancratius"),
    ("Панкратиус", "Pancratius"),
    # Recon groups a credit line as one phrase, so the declined name can't be
    # pinned on its own; match the whole credit to keep the canonical spelling.
    ("передано через Сергея Панкратиуса", "transmitted through Sergei Pancratius"),
)

# Canonical English for the project's recurring value vocabulary, applied wherever
# a cover actually shows the term. Single words so they fit tight value rows —
# recon otherwise glosses (e.g. соборность → "SOBORNOST (COMMUNALITY)"), which
# overflows the layout.
PROJECT_VOCABULARY: tuple[tuple[str, str], ...] = (
    ("Вера", "Faith"),
    ("Соборность", "Conciliarity"),
    ("Справедливость", "Justice"),
    ("Созидание", "Creation"),
)


@dataclass(frozen=True, slots=True)
class ProjectImageTextPlan:
    """Project-provider visible text contract and source-keyed overrides."""

    expected_text: tuple[ExpectedText, ...]
    overrides: tuple[TextOverride, ...]


class ProjectCoverError(ValueError):
    """A project image translation selector cannot be resolved."""


def _project_slug(target: ProjectResourceSelector) -> str:
    if isinstance(target, ProjectSelector):
        return target.slug
    return target.project


def _subpage_slug(target: ProjectResourceSelector) -> str | None:
    if isinstance(target, ProjectSubpageSelector):
        return target.subpage
    return None


def _project_dir(content_root: Path, target: ProjectResourceSelector) -> Path:
    root = content_root / "projects" / _project_slug(target)
    subpage = _subpage_slug(target)
    if subpage is None:
        return root
    return root / "subpages" / subpage


def _project_root(content_root: Path, target: ProjectResourceSelector) -> Path:
    return content_root / "projects" / _project_slug(target)


def _read_frontmatter(path: Path) -> dict[str, Any]:
    fm, _body = split_frontmatter(path.read_text(encoding="utf-8"))
    return fm


def _scalar(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _cover_path(folder: Path, fm: dict[str, Any], md_path: Path) -> Path:
    cover = fm.get("cover")
    if not isinstance(cover, str) or not cover.startswith("./"):
        raise ProjectCoverError(f"{md_path}: missing local cover: ./cover.<lang>.<ext>")
    path = folder / cover[2:]
    if not path.is_file():
        raise ProjectCoverError(f"{md_path}: cover not found: {path}")
    return path


def _target_cover(source_image: Path, target_lang: str) -> Path:
    return source_image.with_name(f"cover.{target_lang}{source_image.suffix.lower()}")


def _replace_frontmatter_scalar(path: Path, key: str, value: str) -> None:
    text = path.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise ProjectCoverError(f"{path}: missing frontmatter")
    lines = match.group(1).splitlines()
    replacement = f"{key}: {value}"
    for i, line in enumerate(lines):
        if re.match(rf"^{re.escape(key)}\s*:", line):
            if line == replacement:
                return
            lines[i] = replacement
            break
    else:
        raise ProjectCoverError(f"{path}: missing existing scalar {key}: frontmatter key")
    body = text[match.end():].lstrip("\n")
    frontmatter = "\n".join(lines)
    path.write_text(f"---\n{frontmatter}\n---\n\n{body}", encoding="utf-8")


def _metadata(target: ProjectResourceSelector, target_fm: dict[str, Any]) -> dict[str, str]:
    subpage = _subpage_slug(target)
    return {
        "kind": "project-cover",
        "project": _project_slug(target),
        "subpage": subpage or "",
        "title_target": _scalar(target_fm.get("title")) or "",
    }


def _required_primary(source: str | None, target: str | None, provenance: str) -> ExpectedText | None:
    if not source or not target:
        return None
    return ExpectedText(
        (NormalizedText(source), RoleSelector(TextRole.PRIMARY)),
        target,
        provenance=provenance,
    )


def _override(source: str | None, target: str | None, provenance: str) -> TextOverride | None:
    if not source or not target:
        return None
    return TextOverride(NormalizedText(source), target, provenance=provenance)


def _common_overrides() -> tuple[TextOverride, ...]:
    # Normalized (case/whitespace-insensitive) so a styled all-caps credit like
    # "СЕРГЕЙ ПАНКРАТИУС" still maps to the canonical "Sergei Pancratius" instead
    # of recon's own transliteration.
    return tuple(
        TextOverride(NormalizedText(source), target, provenance="project-common")
        for source, target in (*COMMON_PROJECT_OVERRIDES, *PROJECT_VOCABULARY)
    )


def _page_overrides(source_fm: dict[str, Any], target_fm: dict[str, Any], *, include_title: bool) -> tuple[TextOverride, ...]:
    overrides: list[TextOverride] = list(_common_overrides())
    if include_title:
        title = _override(_scalar(source_fm.get("title")), _scalar(target_fm.get("title")), "project-page-title")
        if title is not None:
            overrides.append(title)
    tagline = _override(_scalar(source_fm.get("tagline")), _scalar(target_fm.get("tagline")), "project-tagline")
    if tagline is not None:
        overrides.append(tagline)
    return tuple(overrides)


def _landing_plan(source_fm: dict[str, Any], target_fm: dict[str, Any]) -> ProjectImageTextPlan:
    source_title = _scalar(source_fm.get("title"))
    target_title = _scalar(target_fm.get("title"))
    primary = _required_primary(source_title, target_title, "project-title")
    return ProjectImageTextPlan(
        expected_text=() if primary is None else (primary,),
        overrides=_page_overrides(source_fm, target_fm, include_title=False),
    )


def _subpage_plan(
    *,
    parent_source_fm: dict[str, Any],
    parent_target_fm: dict[str, Any],
    source_fm: dict[str, Any],
    target_fm: dict[str, Any],
) -> ProjectImageTextPlan:
    primary = _required_primary(
        _scalar(parent_source_fm.get("title")),
        _scalar(parent_target_fm.get("title")),
        "project-brand",
    )
    return ProjectImageTextPlan(
        expected_text=() if primary is None else (primary,),
        overrides=_page_overrides(source_fm, target_fm, include_title=True),
    )


@dataclass(frozen=True, slots=True)
class ProjectCoverProvider:
    """Build image-translation jobs for project landing and subpage covers."""

    content_root: Path = CONTENT_ROOT
    output_dir: Path = Path("image-translate-out")

    def spec(self, target: ProjectResourceSelector) -> ProviderJob:
        folder = _project_dir(self.content_root, target)
        source_md = folder / "ru.md"
        target_md = folder / "en.md"
        if not source_md.is_file():
            raise ProjectCoverError(f"source project page not found: {source_md}")
        if not target_md.is_file():
            raise ProjectCoverError(f"target project page not found: {target_md}")
        source_fm = _read_frontmatter(source_md)
        target_fm = _read_frontmatter(target_md)
        if _subpage_slug(target) is None:
            text_plan = _landing_plan(source_fm, target_fm)
        else:
            root = _project_root(self.content_root, target)
            parent_source_md = root / "ru.md"
            parent_target_md = root / "en.md"
            if not parent_source_md.is_file():
                raise ProjectCoverError(f"parent project page not found: {parent_source_md}")
            if not parent_target_md.is_file():
                raise ProjectCoverError(f"parent project page not found: {parent_target_md}")
            text_plan = _subpage_plan(
                parent_source_fm=_read_frontmatter(parent_source_md),
                parent_target_fm=_read_frontmatter(parent_target_md),
                source_fm=source_fm,
                target_fm=target_fm,
            )
        source_image = _cover_path(folder, source_fm, source_md)
        if _scalar(target_fm.get("cover")) is None:
            raise ProjectCoverError(f"{target_md}: missing existing scalar cover key")
        target_image = _target_cover(source_image, "en")
        raw_image = self.output_dir / f"{target.key.replace(':', '-').replace('/', '-')}.raw.png"
        rel_target = f"./{target_image.name}"
        job = ImageTranslationJob(
            key=target.key,
            source_image=source_image,
            target_image=target_image,
            raw_image=raw_image,
            expected_text=text_plan.expected_text,
            overrides=text_plan.overrides,
            context="project cover",
            metadata=_metadata(target, target_fm),
        )

        def finalize(_result: ImageTranslationResult) -> None:
            _replace_frontmatter_scalar(target_md, "cover", rel_target)

        return ProviderJob(
            job=job,
            label=target.key,
            finalize_success=finalize,
            frontmatter_updates=(FrontmatterUpdate(target_md, "cover", rel_target),),
        )
