"""Unit tests for generic image text translation and content providers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from pancratius.translation.image.models import (
    DetectedText,
    ExactText,
    ExpectedText,
    ImageTranslationJob,
    ImageTranslationResult,
    ImageTranslationStatus,
    NormalizedText,
    QaDiscrepancy,
    QaVerdict,
    ResolvedText,
    RoleSelector,
    TextOverride,
    TextRole,
)
from pancratius.translation.image.prompts import (
    SteeringLevel,
    build_steering,
    generation_prompt,
    qa_prompt,
)
from pancratius.translation.image.providers.book_cover import (
    AUTHOR_EN,
    AUTHOR_RU,
    BookCoverProvider,
    SeedMap,
    TitlePin,
    TitleSource,
    init_seed,
    load_seed,
    normalise_book_key,
    plan_title,
    resolve_pin,
    resolve_title,
    text_plan_for_book,
)
from pancratius.translation.image.providers.project_cover import (
    ProjectCoverError,
    ProjectCoverProvider,
    parse_project_selector,
)
from pancratius.translation.image.schema import (
    _extract_json,
    parse_qa,
    parse_recon,
    qa_format,
    recon_format,
)
from pancratius.translation.image.translator import ImageTranslationConfig, resolve_texts


def _png_bytes() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01"
        b"\x00\x00\x00\x01"
        b"\x08\x02"
        b"\x00\x00\x00"
        b"\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def _source_image(tmp_path: Path, name: str = "source.ru.png") -> Path:
    path = tmp_path / name
    path.write_bytes(_png_bytes())
    return path


def _job(tmp_path: Path, *, key: str = "book-50") -> ImageTranslationJob:
    return ImageTranslationJob(
        key=key,
        source_image=_source_image(tmp_path),
        target_image=tmp_path / "out" / f"{key}.en.png",
        raw_image=tmp_path / "out" / f"{key}.raw.png",
        expected_text=(
            ExpectedText((RoleSelector(TextRole.PRIMARY),), "Mammon", provenance="test-title"),
            ExpectedText((ExactText(AUTHOR_RU),), AUTHOR_EN, provenance="test-author"),
        ),
        context="test image",
    )


def _job_with_caveat_policy(tmp_path: Path, *, key: str = "book-50") -> ImageTranslationJob:
    base = _job(tmp_path, key=key)
    return ImageTranslationJob(
        key=base.key,
        source_image=base.source_image,
        target_image=base.target_image,
        raw_image=base.raw_image,
        expected_text=base.expected_text,
        overrides=base.overrides,
        context=base.context,
        allow_embedded_text_caveat=True,
    )


def _detected(
    source: str,
    suggested: str,
    *,
    role: TextRole = TextRole.OTHER,
    embedded: bool = False,
) -> DetectedText:
    return DetectedText(role=role, source=source, suggested_target=suggested, embedded=embedded)


def _resolved(
    source: str,
    target: str,
    *,
    role: TextRole = TextRole.OTHER,
    embedded: bool = False,
) -> ResolvedText:
    return ResolvedText(role=role, source=source, target=target, embedded=embedded)


# ---------------------------------------------------------------------------
# Image text plan model
# ---------------------------------------------------------------------------


def test_resolve_texts_uses_expected_role_for_primary_text() -> None:
    elements = resolve_texts(
        (_detected("Мамона", "Money", role=TextRole.PRIMARY),),
        (ExpectedText((RoleSelector(TextRole.PRIMARY),), "Mammon"),),
    )
    assert elements[0].target == "Mammon"


def test_resolve_texts_override_beats_expected_role() -> None:
    elements = resolve_texts(
        (_detected("Мамона", "Money", role=TextRole.PRIMARY),),
        (ExpectedText((RoleSelector(TextRole.PRIMARY),), "Mammon"),),
        overrides=(TextOverride(ExactText("Мамона"), "MAMMON-OVERRIDE"),),
    )
    assert [(e.source, e.target, e.role) for e in elements] == [
        ("Мамона", "MAMMON-OVERRIDE", TextRole.PRIMARY),
    ]


def test_resolve_texts_expected_alternative_source_match_suppresses_role_fallback_synthesis() -> None:
    elements = resolve_texts(
        (_detected("СВЯТАЯ   РУСЬ", "Holy Rus", role=TextRole.PRIMARY),),
        (
            ExpectedText(
                (NormalizedText("Святая Русь"), RoleSelector(TextRole.PRIMARY)),
                "Holy Commonwealth",
            ),
        ),
    )
    assert [(e.source, e.target, e.role) for e in elements] == [
        ("СВЯТАЯ   РУСЬ", "Holy Commonwealth", TextRole.PRIMARY),
    ]


def test_resolve_texts_expected_source_can_claim_split_text_block() -> None:
    elements = resolve_texts(
        (
            _detected("СВЯТАЯ", "Holy", role=TextRole.PRIMARY),
            _detected("РУСЬ", "Rus'", role=TextRole.SECONDARY),
            _detected("Проект Света", "Project of Light", role=TextRole.TAGLINE),
        ),
        (
            ExpectedText(
                (NormalizedText("Святая Русь"), RoleSelector(TextRole.PRIMARY)),
                "Holy Commonwealth",
            ),
        ),
    )
    assert [(e.source, e.target, e.role) for e in elements] == [
        ("СВЯТАЯ\nРУСЬ", "Holy Commonwealth", TextRole.PRIMARY),
        ("Проект Света", "Project of Light", TextRole.TAGLINE),
    ]


def test_resolve_texts_expected_alternative_matches_one_detected_element_only() -> None:
    elements = resolve_texts(
        (
            _detected("Тартария", "Tartaria", role=TextRole.PRIMARY),
            _detected("СВЯТАЯ   РУСЬ", "Holy Rus", role=TextRole.LABEL),
        ),
        (
            ExpectedText(
                (NormalizedText("Святая Русь"), RoleSelector(TextRole.PRIMARY)),
                "Holy Commonwealth",
            ),
        ),
        overrides=(TextOverride(NormalizedText("Тартария"), "Tartaria"),),
    )
    assert [(e.source, e.target, e.role) for e in elements] == [
        ("Тартария", "Tartaria", TextRole.PRIMARY),
        ("СВЯТАЯ   РУСЬ", "Holy Commonwealth", TextRole.LABEL),
    ]


def test_resolve_texts_normalized_source_override() -> None:
    elements = resolve_texts(
        (_detected("СВЯТАЯ   РУСЬ", "Holy Rus", role=TextRole.PRIMARY),),
        (),
        overrides=(TextOverride(NormalizedText("Святая Русь"), "Holy Commonwealth"),),
    )
    assert elements[0].target == "Holy Commonwealth"


def test_resolve_texts_falls_back_to_recon_translation() -> None:
    elements = resolve_texts(
        (_detected("в его власти", "In His Power", role=TextRole.TAGLINE),),
        (),
    )
    assert elements[0].target == "In His Power"


def test_resolve_texts_synthesizes_expected_text_when_recon_empty() -> None:
    elements = resolve_texts(
        (),
        (
            ExpectedText((ExactText(AUTHOR_RU),), AUTHOR_EN),
            ExpectedText((RoleSelector(TextRole.PRIMARY),), "Holy Commonwealth"),
        ),
    )
    assert [(e.source, e.target, e.role) for e in elements] == [
        (AUTHOR_RU, AUTHOR_EN, TextRole.OTHER),
        ("", "Holy Commonwealth", TextRole.PRIMARY),
    ]


def test_resolve_texts_synthesizes_unmatched_expected_text_on_partial_recon() -> None:
    elements = resolve_texts(
        (_detected("в его власти", "In His Power", role=TextRole.TAGLINE),),
        (
            ExpectedText((ExactText(AUTHOR_RU),), AUTHOR_EN),
            ExpectedText((RoleSelector(TextRole.PRIMARY),), "Mammon"),
        ),
    )
    assert [(e.source, e.target, e.role) for e in elements] == [
        ("в его власти", "In His Power", TextRole.TAGLINE),
        (AUTHOR_RU, AUTHOR_EN, TextRole.OTHER),
        ("", "Mammon", TextRole.PRIMARY),
    ]


def test_resolve_texts_does_not_synthesize_unmatched_override() -> None:
    elements = resolve_texts(
        (_detected("Мамона", "Mammon", role=TextRole.PRIMARY),),
        (),
        overrides=(TextOverride(ExactText("not visible"), "Not Visible"),),
    )
    assert [(e.source, e.target) for e in elements] == [("Мамона", "Mammon")]


def test_image_translation_job_has_expected_text_and_overrides_not_headline(tmp_path: Path) -> None:
    job = _job(tmp_path)
    assert hasattr(job, "expected_text")
    assert hasattr(job, "overrides")
    assert not hasattr(job, "constraints")
    assert not hasattr(job, "headline")


# ---------------------------------------------------------------------------
# Book provider
# ---------------------------------------------------------------------------


def test_normalise_book_key_variants() -> None:
    assert normalise_book_key("book-5") == "book-05"
    assert normalise_book_key("book:7") == "book-07"


def test_resolve_pin_prefers_enmd(tmp_path: Path) -> None:
    books = tmp_path / "books" / "50-mamona-test"
    books.mkdir(parents=True)
    (books / "en.md").write_text(
        "---\ntitle: 'Mammon: Why You Are in Its Power'\nlang: en\n---\n",
        encoding="utf-8",
    )
    seed = SeedMap(titles={"book-50": "Seed Title"}, overrides={})
    pin = resolve_pin(
        "book-50", books_root=tmp_path / "books", queue_titles={"book-50": "Queue"}, seed=seed
    )
    assert pin == TitlePin(wording="Mammon: Why You Are in Its Power", source=TitleSource.EN_MD)


def test_resolve_title_derives_primary_text_short_form(tmp_path: Path) -> None:
    books = tmp_path / "books" / "01-test"
    books.mkdir(parents=True)
    (books / "en.md").write_text(
        "---\ntitle: 'Gospel of the One: I Am'\nlang: en\n---\n",
        encoding="utf-8",
    )
    title = resolve_title(
        "book-01", books_root=tmp_path / "books", queue_titles={}, seed=SeedMap(titles={}, overrides={})
    )
    assert title.to_render == "Gospel of the One"
    assert title.authoritative_wording == "Gospel of the One: I Am"


def test_plan_title_no_pin_is_model_translated() -> None:
    plan = plan_title(None)
    assert plan.to_render == ""
    assert plan.source == TitleSource.MODEL
    assert not plan.is_pinned


def test_text_plan_for_book_seed_override_author_and_primary_title() -> None:
    plan = text_plan_for_book(
        plan_title(TitlePin(wording="Mammon: Why You Are in His Power", source=TitleSource.EN_MD)),
        SeedMap(titles={}, overrides={"Система дефицита": "System of Scarcity"}),
    )
    assert ExpectedText((ExactText(AUTHOR_RU),), AUTHOR_EN, provenance="book-author") in plan.expected_text
    assert any(
        o.selector == ExactText("Система дефицита") and o.target == "System of Scarcity"
        for o in plan.overrides
    )
    assert any(
        e.selectors == (RoleSelector(TextRole.PRIMARY),) and e.target == "Mammon"
        for e in plan.expected_text
    )


def test_book_provider_builds_generic_job(tmp_path: Path) -> None:
    covers = tmp_path / "covers"
    covers.mkdir()
    (covers / "book-50.ru.png").write_bytes(_png_bytes())
    provider = BookCoverProvider(
        output_dir=tmp_path / "out",
        covers_dir=covers,
        queue_md=tmp_path / "QUEUE.md",
        books_root=tmp_path / "books",
        seed_path=tmp_path / "seed.json",
    )
    spec = provider.spec("book:50")
    assert spec.job.key == "book-50"
    assert spec.job.source_image == covers / "book-50.ru.png"
    assert spec.job.target_image == tmp_path / "out" / "book-50.en.png"
    assert any(e.selectors == (ExactText(AUTHOR_RU),) for e in spec.job.expected_text)


def test_load_seed_returns_empty_when_absent(tmp_path: Path) -> None:
    seed_path = tmp_path / "seed.json"
    seed = load_seed(seed_path)
    assert seed.titles == {}
    assert seed.overrides == {}
    assert not seed_path.exists()


def test_init_seed_is_idempotent(tmp_path: Path) -> None:
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(json.dumps({"titles": {"book-01": "X"}, "overrides": {}}))
    init_seed(seed_path)
    raw = json.loads(seed_path.read_text())
    assert raw["titles"] == {"book-01": "X"}


# ---------------------------------------------------------------------------
# Project provider
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "selector",
    [
        "project:../holy-rus",
        "project:/holy-rus",
        "project:holy-rus/..",
        "project:holy-rus/../../escape",
        "project:holy_rus",
        "project:holy-rus/sub_page",
    ],
)
def test_project_selector_rejects_non_slug_path_fragments(selector: str) -> None:
    with pytest.raises(ProjectCoverError, match="invalid project selector"):
        parse_project_selector(selector)


def test_project_provider_builds_job_and_finalizer_updates_en_cover(tmp_path: Path) -> None:
    folder = tmp_path / "projects" / "holy-rus"
    folder.mkdir(parents=True)
    (folder / "cover.ru.png").write_bytes(_png_bytes())
    (folder / "ru.md").write_text(
        "---\nkind: project\nslug: holy-rus\ntitle: Святая Русь\nlang: ru\n"
        "description: x\ncover: ./cover.ru.png\ntagline: Русская строка\n---\n\nbody",
        encoding="utf-8",
    )
    en = folder / "en.md"
    en.write_text(
        "---\nkind: project\nslug: holy-rus\ntitle: Holy Commonwealth\nlang: en\n"
        "description: x\ncover: ./cover.ru.png\ntagline: English line\n---\n\nbody",
        encoding="utf-8",
    )
    spec = ProjectCoverProvider(content_root=tmp_path, output_dir=tmp_path / "out").spec("project:holy-rus")
    assert spec.job.key == "project:holy-rus"
    assert spec.job.target_image == folder / "cover.en.png"
    assert [(u.path, u.field, u.value) for u in spec.frontmatter_updates] == [
        (en, "cover", "./cover.en.png")
    ]
    assert any(
        e.selectors == (NormalizedText("Святая Русь"), RoleSelector(TextRole.PRIMARY))
        and e.target == "Holy Commonwealth"
        for e in spec.job.expected_text
    )
    assert any(
        o.selector == NormalizedText("Русская строка") and o.target == "English line"
        for o in spec.job.overrides
    )
    resolved = resolve_texts(
        (
            _detected("СВЯТАЯ   РУСЬ", "Holy Rus", role=TextRole.PRIMARY),
            _detected("Русская строка", "Russian line", role=TextRole.TAGLINE),
        ),
        spec.job.expected_text,
        overrides=spec.job.overrides,
    )
    assert [(e.source, e.target, e.role) for e in resolved] == [
        ("СВЯТАЯ   РУСЬ", "Holy Commonwealth", TextRole.PRIMARY),
        ("Русская строка", "English line", TextRole.TAGLINE),
    ]
    with_name = resolve_texts(
        (
            _detected("СВЯТАЯ   РУСЬ", "Holy Rus", role=TextRole.PRIMARY),
            _detected("Сергей Панкратиус", "Sergey Pankratius", role=TextRole.OTHER),
        ),
        spec.job.expected_text,
        overrides=spec.job.overrides,
    )
    assert [(e.source, e.target, e.role) for e in with_name] == [
        ("СВЯТАЯ   РУСЬ", "Holy Commonwealth", TextRole.PRIMARY),
        ("Сергей Панкратиус", "Sergei Pancratius", TextRole.OTHER),
    ]

    result = ImageTranslationResult(
        key=spec.job.key,
        status=ImageTranslationStatus.OK,
        final_path=spec.job.target_image,
        raw_path=spec.job.raw_image,
        attempts=(),
        primary_text=None,
        error=None,
        total_cost_usd=0.0,
    )
    spec.finalize(result)
    assert "cover: ./cover.en.png" in en.read_text(encoding="utf-8")


def test_project_provider_requires_existing_target_cover_key(tmp_path: Path) -> None:
    folder = tmp_path / "projects" / "holy-rus"
    folder.mkdir(parents=True)
    (folder / "cover.ru.png").write_bytes(_png_bytes())
    (folder / "ru.md").write_text(
        "---\nkind: project\nslug: holy-rus\ntitle: Святая Русь\nlang: ru\n"
        "description: x\ncover: ./cover.ru.png\n---\n\nbody",
        encoding="utf-8",
    )
    (folder / "en.md").write_text(
        "---\nkind: project\nslug: holy-rus\ntitle: Holy Commonwealth\nlang: en\n"
        "description: |\n  block text\n---\n\nbody",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing existing scalar cover"):
        ProjectCoverProvider(content_root=tmp_path, output_dir=tmp_path / "out").spec("project:holy-rus")


def test_project_subpage_selector_uses_subpage_folder(tmp_path: Path) -> None:
    root = tmp_path / "projects" / "holy-rus"
    root.mkdir(parents=True)
    (root / "ru.md").write_text(
        "---\nkind: project\nslug: holy-rus\ntitle: Святая Русь\nlang: ru\n"
        "description: x\ncover: ./cover.ru.png\n---\n\nbody",
        encoding="utf-8",
    )
    (root / "en.md").write_text(
        "---\nkind: project\nslug: holy-rus\ntitle: Holy Commonwealth\nlang: en\n"
        "description: x\ncover: ./cover.en.png\n---\n\nbody",
        encoding="utf-8",
    )
    folder = root / "subpages" / "tartaria"
    folder.mkdir(parents=True)
    (folder / "cover.ru.jpg").write_bytes(_png_bytes())
    (folder / "ru.md").write_text(
        "---\nkind: project_subpage\nparent: holy-rus\nslug: tartaria\n"
        "title: Тартария\nlang: ru\ndescription: x\ncover: ./cover.ru.jpg\n---\n\nbody",
        encoding="utf-8",
    )
    (folder / "en.md").write_text(
        "---\nkind: project_subpage\nparent: holy-rus\nslug: tartaria\n"
        "title: Tartaria\nlang: en\ndescription: x\ncover: ./cover.ru.jpg\n---\n\nbody",
        encoding="utf-8",
    )
    spec = ProjectCoverProvider(content_root=tmp_path, output_dir=tmp_path / "out").spec("project:holy-rus/tartaria")
    assert spec.job.key == "project:holy-rus/tartaria"
    assert spec.job.source_image == folder / "cover.ru.jpg"
    assert spec.job.target_image == folder / "cover.en.jpg"
    assert any(
        e.selectors == (NormalizedText("Святая Русь"), RoleSelector(TextRole.PRIMARY))
        and e.target == "Holy Commonwealth"
        and e.provenance == "project-brand"
        for e in spec.job.expected_text
    )
    assert any(
        o.selector == NormalizedText("Тартария") and o.target == "Tartaria"
        for o in spec.job.overrides
    )
    brand_only = resolve_texts(
        (_detected("СВЯТАЯ Русь", "Holy Rus", role=TextRole.PRIMARY),),
        spec.job.expected_text,
        overrides=spec.job.overrides,
    )
    assert [(e.source, e.target, e.role) for e in brand_only] == [
        ("СВЯТАЯ Русь", "Holy Commonwealth", TextRole.PRIMARY),
    ]
    split_brand = resolve_texts(
        (
            _detected("СВЯТАЯ", "Holy", role=TextRole.PRIMARY),
            _detected("РУСЬ", "Rus'", role=TextRole.SECONDARY),
            _detected("Тартария", "Tartaria?", role=TextRole.LABEL),
        ),
        spec.job.expected_text,
        overrides=spec.job.overrides,
    )
    assert [(e.source, e.target, e.role) for e in split_brand] == [
        ("СВЯТАЯ\nРУСЬ", "Holy Commonwealth", TextRole.PRIMARY),
        ("Тартария", "Tartaria", TextRole.LABEL),
    ]
    with_subpage_title = resolve_texts(
        (
            _detected("СВЯТАЯ Русь", "Holy Rus", role=TextRole.PRIMARY),
            _detected("Тартария", "Tartaria?", role=TextRole.LABEL),
        ),
        spec.job.expected_text,
        overrides=spec.job.overrides,
    )
    assert [(e.source, e.target, e.role) for e in with_subpage_title] == [
        ("СВЯТАЯ Русь", "Holy Commonwealth", TextRole.PRIMARY),
        ("Тартария", "Tartaria", TextRole.LABEL),
    ]
    title_primary_brand_label = resolve_texts(
        (
            _detected("Тартария", "Tartaria?", role=TextRole.PRIMARY),
            _detected("СВЯТАЯ Русь", "Holy Rus", role=TextRole.LABEL),
        ),
        spec.job.expected_text,
        overrides=spec.job.overrides,
    )
    assert [(e.source, e.target, e.role) for e in title_primary_brand_label] == [
        ("Тартария", "Tartaria", TextRole.PRIMARY),
        ("СВЯТАЯ Русь", "Holy Commonwealth", TextRole.LABEL),
    ]


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------


def test_generation_prompt_has_explicit_per_element_map(tmp_path: Path) -> None:
    job = _job(tmp_path)
    prompt = generation_prompt(
        job=job,
        elements=(
            _resolved("Мамона", "Mammon", role=TextRole.PRIMARY),
            _resolved("в его власти", "In His Power", role=TextRole.TAGLINE),
        ),
        steering="",
    )
    assert "Replace «Мамона» with «Mammon»" in prompt
    assert "Replace «в его власти» with «In His Power»" in prompt
    assert "headline" not in prompt.lower()


def test_generation_prompt_treats_split_source_as_one_text_block(tmp_path: Path) -> None:
    prompt = generation_prompt(
        job=_job(tmp_path),
        elements=(
            _resolved("СВЯТАЯ\nРУСЬ", "Holy Commonwealth", role=TextRole.PRIMARY),
            _resolved("Проект Света", "Project of Light", role=TextRole.TAGLINE),
        ),
        steering="",
    )
    assert "multi-line" in prompt
    assert "«СВЯТАЯ / РУСЬ»" in prompt
    assert "single target string «Holy Commonwealth»" in prompt
    assert "do not translate the lines separately" in prompt


def test_generation_prompt_flags_embedded_element(tmp_path: Path) -> None:
    prompt = generation_prompt(
        job=_job(tmp_path),
        elements=(_resolved("Система дефицита", "System of Scarcity", embedded=True),),
        steering="",
    )
    assert "embedded artwork text" in prompt
    assert "System of Scarcity" in prompt


def test_qa_prompt_lists_expected_target_strings(tmp_path: Path) -> None:
    prompt = qa_prompt(
        job=_job(tmp_path),
        elements=(_resolved("Сергей Панкратиус", AUTHOR_EN, role=TextRole.CREDIT),),
    )
    assert AUTHOR_EN in prompt
    assert "Russian/Cyrillic" in prompt


def test_build_steering_names_targets_not_source_text() -> None:
    steering = build_steering(
        [QaDiscrepancy(kind="source_text_left", description="«Система дефицита» remains")],
        elements=(_resolved("Система дефицита", "System of Scarcity", embedded=True),),
    )
    assert "System of Scarcity" in steering
    assert "Система дефицита" not in steering


def test_build_steering_escalates_to_urgent() -> None:
    discrepancies = [QaDiscrepancy(kind="source_text_left", description="still source text")]
    firm = build_steering(discrepancies, level=SteeringLevel.FIRM)
    urgent = build_steering(discrepancies, level=SteeringLevel.URGENT)
    assert firm != urgent
    assert "CRITICAL" in urgent


# ---------------------------------------------------------------------------
# Schema parsing
# ---------------------------------------------------------------------------


def test_recon_format_is_strict_json_schema() -> None:
    fmt = recon_format()
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    schema = fmt["json_schema"]["schema"]
    assert "elements" in schema["properties"]
    assert "primary_text" in schema["properties"]


def test_parse_recon_extracts_text_role_and_embedded_flag() -> None:
    text = json.dumps({
        "elements": [
            {"role": "primary", "source_text": "Мамона", "target_text": "Mammon", "embedded": False},
            {"role": "art_text", "source_text": "Система дефицита", "target_text": "System of Scarcity", "embedded": True},
        ],
        "primary_text": "Мамона",
    })
    recon = parse_recon(text)
    assert [(e.role, e.source, e.suggested_target, e.embedded) for e in recon.elements] == [
        (TextRole.PRIMARY, "Мамона", "Mammon", False),
        (TextRole.ART_TEXT, "Система дефицита", "System of Scarcity", True),
    ]
    assert recon.primary_text == "Мамона"


def test_parse_recon_accepts_legacy_title_role_as_primary() -> None:
    text = json.dumps({
        "elements": [{"role": "title", "russian": "Мамона", "english": "Mammon", "art_baked": False}],
        "displayed_title": "Мамона",
    })
    recon = parse_recon(text)
    assert recon.elements[0].role is TextRole.PRIMARY
    assert recon.primary_text == "Мамона"


def test_qa_format_is_strict_json_schema() -> None:
    fmt = qa_format()
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True


def test_parse_qa_pass_returns_empty_discrepancies() -> None:
    qa = parse_qa(json.dumps({"verdict": "pass", "discrepancies": []}))
    assert qa.verdict == QaVerdict.PASS
    assert qa.discrepancies == ()


def test_parse_qa_unknown_verdict_fails_closed() -> None:
    qa = parse_qa(json.dumps({"verdict": "passed-ish", "discrepancies": []}))
    assert qa.verdict == QaVerdict.FAIL


def test_parse_qa_maps_legacy_cyrillic_left_kind() -> None:
    qa = parse_qa(json.dumps({
        "verdict": "fail",
        "discrepancies": [{"kind": "cyrillic_left", "description": "x", "in_artwork": True}],
    }))
    assert qa.discrepancies == (QaDiscrepancy(kind="source_text_left", description="x", embedded=True),)


def test_extract_json_extra_braces_after() -> None:
    text = 'Here is the result: {"verdict": "pass", "discrepancies": []} extra {stuff}'
    assert json.loads(_extract_json(text)) == {"verdict": "pass", "discrepancies": []}


def test_parse_qa_fail_on_unparseable_response() -> None:
    junk = 'I cannot parse this. A good response would have "verdict": "pass" but I see errors.'
    with pytest.raises(json.JSONDecodeError):
        parse_qa(junk)


# ---------------------------------------------------------------------------
# Pipeline integration with fake client
# ---------------------------------------------------------------------------


@dataclass
class _FakeVisionResponse:
    text: str
    cost_usd: float = 0.001
    usage: dict[str, object] = field(default_factory=dict)


@dataclass
class _FakeGenerationResponse:
    image_bytes: bytes = field(default_factory=_png_bytes)
    cost_usd: float = 0.068
    usage: dict[str, object] = field(default_factory=dict)


def _make_recon_json(primary: str = "Мамона") -> str:
    return json.dumps({
        "elements": [
            {"role": "primary", "source_text": primary, "target_text": "Mammon", "embedded": False},
            {"role": "author", "source_text": AUTHOR_RU, "target_text": AUTHOR_EN, "embedded": False},
        ],
        "primary_text": primary,
    })


def _make_qa_json(verdict: str, kinds: list[str] | None = None, *, embedded: bool = False) -> str:
    discs = [{"kind": k, "description": f"defect: {k}", "embedded": embedded} for k in (kinds or [])]
    return json.dumps({"verdict": verdict, "discrepancies": discs})


def test_pipeline_pass_on_attempt_n_stops_loop(tmp_path: Path) -> None:
    from pancratius.translation.image.decrop import DecropReport
    from pancratius.translation.image.translator import translate_image

    job = _job(tmp_path)
    qa_responses = iter([
        _make_qa_json("fail", ["source_text_left"]),
        _make_qa_json("pass"),
    ])

    def fake_vision_text(
        *,
        images: list[Path],  # noqa: ARG001
        prompt: str,
        api_key: str,  # noqa: ARG001
        response_format: object = None,  # noqa: ARG001
    ) -> _FakeVisionResponse:
        if "quality assurance" in prompt:
            return _FakeVisionResponse(text=next(qa_responses))
        return _FakeVisionResponse(text=_make_recon_json())

    def fake_decrop(
        raw_bytes: bytes,  # noqa: ARG001
        source: Path,  # noqa: ARG001
        raw_out: Path,
        final_out: Path,
    ) -> DecropReport:
        raw_out.parent.mkdir(parents=True, exist_ok=True)
        raw_out.write_bytes(b"fake-raw")
        final_out.write_bytes(b"fake-final")
        return DecropReport(source_size=(1, 1), raw_size=(1, 1), final_size=(1, 1), resized=False)

    with (
        patch("pancratius.translation.image.translator.generate_image_translation", return_value=_FakeGenerationResponse()),
        patch("pancratius.translation.image.translator.vision_text", side_effect=fake_vision_text),
        patch("pancratius.translation.image.translator.decrop_to_source", side_effect=fake_decrop),
    ):
        result = translate_image(job, ImageTranslationConfig(max_attempts=3, inter_call_sleep=0.0), "fake-key")

    assert result.ok
    assert len(result.attempts) == 2
    assert result.attempts[-1].qa.verdict == QaVerdict.PASS


def test_pipeline_existing_target_qa_fail_refuses_without_replace(tmp_path: Path) -> None:
    from pancratius.translation.image.translator import translate_image

    job = _job(tmp_path)
    job.target_image.parent.mkdir(parents=True)
    job.target_image.write_bytes(b"existing-target")

    def fake_vision_text(
        *,
        images: list[Path],  # noqa: ARG001
        prompt: str,
        api_key: str,  # noqa: ARG001
        response_format: object = None,  # noqa: ARG001
    ) -> _FakeVisionResponse:
        if "quality assurance" in prompt:
            return _FakeVisionResponse(text=_make_qa_json("fail", ["wrong_text"]))
        return _FakeVisionResponse(text=_make_recon_json())

    with (
        patch("pancratius.translation.image.translator.generate_image_translation", side_effect=AssertionError("generated")),
        patch("pancratius.translation.image.translator.vision_text", side_effect=fake_vision_text),
    ):
        result = translate_image(job, ImageTranslationConfig(max_attempts=1, inter_call_sleep=0.0), "fake-key")

    assert result.status is ImageTranslationStatus.FAIL
    assert "pass --replace" in (result.error or "")
    assert len(result.attempts) == 1
    assert result.attempts[0].attempt == 0
    assert job.target_image.read_bytes() == b"existing-target"
    assert not job.raw_output().exists()


def test_pipeline_replace_regenerates_existing_target_after_failed_qa(tmp_path: Path) -> None:
    from pancratius.translation.image.decrop import DecropReport
    from pancratius.translation.image.translator import translate_image

    job = _job(tmp_path)
    job.target_image.parent.mkdir(parents=True)
    job.target_image.write_bytes(b"existing-target")
    generated = 0
    qa_responses = iter([
        _make_qa_json("fail", ["wrong_text"]),
        _make_qa_json("pass"),
    ])

    def fake_generate(src: Path, prompt: str, api_key: str, *, model: str | None = None) -> _FakeGenerationResponse:  # noqa: ARG001
        nonlocal generated
        generated += 1
        return _FakeGenerationResponse()

    def fake_vision_text(
        *,
        images: list[Path],  # noqa: ARG001
        prompt: str,
        api_key: str,  # noqa: ARG001
        response_format: object = None,  # noqa: ARG001
    ) -> _FakeVisionResponse:
        if "quality assurance" in prompt:
            return _FakeVisionResponse(text=next(qa_responses))
        return _FakeVisionResponse(text=_make_recon_json())

    def fake_decrop(
        raw_bytes: bytes,  # noqa: ARG001
        source: Path,  # noqa: ARG001
        raw_out: Path,
        final_out: Path,
    ) -> DecropReport:
        raw_out.parent.mkdir(parents=True, exist_ok=True)
        raw_out.write_bytes(b"fake-raw")
        final_out.write_bytes(b"new-target")
        return DecropReport(source_size=(1, 1), raw_size=(1, 1), final_size=(1, 1), resized=False)

    with (
        patch("pancratius.translation.image.translator.generate_image_translation", side_effect=fake_generate),
        patch("pancratius.translation.image.translator.vision_text", side_effect=fake_vision_text),
        patch("pancratius.translation.image.translator.decrop_to_source", side_effect=fake_decrop),
    ):
        result = translate_image(
            job,
            ImageTranslationConfig(max_attempts=1, inter_call_sleep=0.0, replace_existing=True),
            "fake-key",
        )

    assert result.ok
    assert generated == 1
    assert job.target_image.read_bytes() == b"new-target"


def test_pipeline_replace_failure_preserves_existing_target(tmp_path: Path) -> None:
    from pancratius.translation.image.decrop import DecropReport
    from pancratius.translation.image.translator import translate_image

    job = _job(tmp_path)
    job.target_image.parent.mkdir(parents=True)
    job.target_image.write_bytes(b"existing-target")
    qa_responses = iter([
        _make_qa_json("fail", ["wrong_text"]),
        _make_qa_json("fail", ["wrong_text"]),
    ])

    def fake_vision_text(
        *,
        images: list[Path],  # noqa: ARG001
        prompt: str,
        api_key: str,  # noqa: ARG001
        response_format: object = None,  # noqa: ARG001
    ) -> _FakeVisionResponse:
        if "quality assurance" in prompt:
            return _FakeVisionResponse(text=next(qa_responses))
        return _FakeVisionResponse(text=_make_recon_json())

    def fake_decrop(
        raw_bytes: bytes,  # noqa: ARG001
        source: Path,  # noqa: ARG001
        raw_out: Path,
        final_out: Path,
    ) -> DecropReport:
        raw_out.parent.mkdir(parents=True, exist_ok=True)
        raw_out.write_bytes(b"fake-raw")
        final_out.write_bytes(b"bad-replacement")
        return DecropReport(source_size=(1, 1), raw_size=(1, 1), final_size=(1, 1), resized=False)

    with (
        patch("pancratius.translation.image.translator.generate_image_translation", return_value=_FakeGenerationResponse()),
        patch("pancratius.translation.image.translator.vision_text", side_effect=fake_vision_text),
        patch("pancratius.translation.image.translator.decrop_to_source", side_effect=fake_decrop),
    ):
        result = translate_image(
            job,
            ImageTranslationConfig(max_attempts=1, inter_call_sleep=0.0, replace_existing=True),
            "fake-key",
        )

    assert result.status is ImageTranslationStatus.FAIL
    assert job.target_image.read_bytes() == b"existing-target"
    assert not job.target_image.with_name("book-50.en.replace.png").exists()
    assert not job.raw_output().with_name("book-50.raw.replace.png").exists()


def test_pipeline_replace_generation_error_removes_staged_outputs(tmp_path: Path) -> None:
    from pancratius.translation.image.client import ImageTranslationClientError
    from pancratius.translation.image.decrop import DecropReport
    from pancratius.translation.image.translator import translate_image

    job = _job(tmp_path)
    job.target_image.parent.mkdir(parents=True)
    job.target_image.write_bytes(b"existing-target")
    qa_responses = iter([
        _make_qa_json("fail", ["wrong_text"]),
        _make_qa_json("fail", ["wrong_text"]),
    ])
    generation_responses = iter([
        _FakeGenerationResponse(),
        ImageTranslationClientError("api failed"),
    ])

    def fake_generate(src: Path, prompt: str, api_key: str, *, model: str | None = None) -> _FakeGenerationResponse:  # noqa: ARG001
        response = next(generation_responses)
        if isinstance(response, Exception):
            raise response
        return response

    def fake_vision_text(
        *,
        images: list[Path],  # noqa: ARG001
        prompt: str,
        api_key: str,  # noqa: ARG001
        response_format: object = None,  # noqa: ARG001
    ) -> _FakeVisionResponse:
        if "quality assurance" in prompt:
            return _FakeVisionResponse(text=next(qa_responses))
        return _FakeVisionResponse(text=_make_recon_json())

    def fake_decrop(
        raw_bytes: bytes,  # noqa: ARG001
        source: Path,  # noqa: ARG001
        raw_out: Path,
        final_out: Path,
    ) -> DecropReport:
        raw_out.parent.mkdir(parents=True, exist_ok=True)
        raw_out.write_bytes(b"fake-raw")
        final_out.write_bytes(b"bad-replacement")
        return DecropReport(source_size=(1, 1), raw_size=(1, 1), final_size=(1, 1), resized=False)

    with (
        patch("pancratius.translation.image.translator.generate_image_translation", side_effect=fake_generate),
        patch("pancratius.translation.image.translator.vision_text", side_effect=fake_vision_text),
        patch("pancratius.translation.image.translator.decrop_to_source", side_effect=fake_decrop),
    ):
        result = translate_image(
            job,
            ImageTranslationConfig(max_attempts=2, inter_call_sleep=0.0, replace_existing=True),
            "fake-key",
        )

    assert result.status is ImageTranslationStatus.FAIL
    assert "generation failed" in (result.error or "")
    assert job.target_image.read_bytes() == b"existing-target"
    assert not job.target_image.with_name("book-50.en.replace.png").exists()
    assert not job.raw_output().with_name("book-50.raw.replace.png").exists()


def test_pipeline_replace_qa_credit_error_removes_staged_outputs(tmp_path: Path) -> None:
    from pancratius.translation.image.client import InsufficientCreditsError
    from pancratius.translation.image.decrop import DecropReport
    from pancratius.translation.image.translator import translate_image

    job = _job(tmp_path)
    job.target_image.parent.mkdir(parents=True)
    job.target_image.write_bytes(b"existing-target")
    qa_calls = 0

    def fake_vision_text(
        *,
        images: list[Path],  # noqa: ARG001
        prompt: str,
        api_key: str,  # noqa: ARG001
        response_format: object = None,  # noqa: ARG001
    ) -> _FakeVisionResponse:
        nonlocal qa_calls
        if "quality assurance" in prompt:
            qa_calls += 1
            if qa_calls == 1:
                return _FakeVisionResponse(text=_make_qa_json("fail", ["wrong_text"]))
            raise InsufficientCreditsError("HTTP 402: no credits")
        return _FakeVisionResponse(text=_make_recon_json())

    def fake_decrop(
        raw_bytes: bytes,  # noqa: ARG001
        source: Path,  # noqa: ARG001
        raw_out: Path,
        final_out: Path,
    ) -> DecropReport:
        raw_out.parent.mkdir(parents=True, exist_ok=True)
        raw_out.write_bytes(b"fake-raw")
        final_out.write_bytes(b"candidate")
        return DecropReport(source_size=(1, 1), raw_size=(1, 1), final_size=(1, 1), resized=False)

    with (
        patch("pancratius.translation.image.translator.generate_image_translation", return_value=_FakeGenerationResponse()),
        patch("pancratius.translation.image.translator.vision_text", side_effect=fake_vision_text),
        patch("pancratius.translation.image.translator.decrop_to_source", side_effect=fake_decrop),
        pytest.raises(InsufficientCreditsError),
    ):
        translate_image(
            job,
            ImageTranslationConfig(max_attempts=1, inter_call_sleep=0.0, replace_existing=True),
            "fake-key",
        )

    assert job.target_image.read_bytes() == b"existing-target"
    assert not job.target_image.with_name("book-50.en.replace.png").exists()
    assert not job.raw_output().with_name("book-50.raw.replace.png").exists()


def test_pipeline_retry_edits_previous_output_not_raw_source(tmp_path: Path) -> None:
    from pancratius.translation.image.decrop import DecropReport
    from pancratius.translation.image.translator import translate_image

    job = _job(tmp_path)
    edit_bases: list[Path] = []

    def fake_generate(src: Path, prompt: str, api_key: str, *, model: str | None = None) -> _FakeGenerationResponse:  # noqa: ARG001
        edit_bases.append(src)
        return _FakeGenerationResponse()

    qa_responses = iter([
        _make_qa_json("fail", ["source_text_left"]),
        _make_qa_json("pass"),
    ])

    def fake_vision_text(
        *,
        images: list[Path],  # noqa: ARG001
        prompt: str,
        api_key: str,  # noqa: ARG001
        response_format: object = None,  # noqa: ARG001
    ) -> _FakeVisionResponse:
        if "quality assurance" in prompt:
            return _FakeVisionResponse(text=next(qa_responses))
        return _FakeVisionResponse(text=_make_recon_json())

    def fake_decrop(
        raw_bytes: bytes,  # noqa: ARG001
        source: Path,  # noqa: ARG001
        raw_out: Path,
        final_out: Path,
    ) -> DecropReport:
        raw_out.parent.mkdir(parents=True, exist_ok=True)
        raw_out.write_bytes(b"fake-raw")
        final_out.write_bytes(b"fake-final")
        return DecropReport(source_size=(1, 1), raw_size=(1, 1), final_size=(1, 1), resized=False)

    with (
        patch("pancratius.translation.image.translator.generate_image_translation", side_effect=fake_generate),
        patch("pancratius.translation.image.translator.vision_text", side_effect=fake_vision_text),
        patch("pancratius.translation.image.translator.decrop_to_source", side_effect=fake_decrop),
    ):
        result = translate_image(job, ImageTranslationConfig(max_attempts=3, inter_call_sleep=0.0), "fake-key")

    assert result.ok
    assert edit_bases[0] == job.source_image
    assert edit_bases[1] != job.source_image
    assert edit_bases[1].name == "book-50.prev.png"
    assert not (job.raw_output().parent / "book-50.prev.png").exists()


def test_pipeline_persistent_fail_exhausts_attempts_and_unlinks(tmp_path: Path) -> None:
    from pancratius.translation.image.decrop import DecropReport
    from pancratius.translation.image.translator import translate_image

    job = _job(tmp_path)

    def fake_vision_text(
        *,
        images: list[Path],  # noqa: ARG001
        prompt: str,
        api_key: str,  # noqa: ARG001
        response_format: object = None,  # noqa: ARG001
    ) -> _FakeVisionResponse:
        if "quality assurance" in prompt:
            return _FakeVisionResponse(text=_make_qa_json("fail", ["source_text_left"]))
        return _FakeVisionResponse(text=_make_recon_json())

    def fake_decrop(
        raw_bytes: bytes,  # noqa: ARG001
        source: Path,  # noqa: ARG001
        raw_out: Path,
        final_out: Path,
    ) -> DecropReport:
        raw_out.parent.mkdir(parents=True, exist_ok=True)
        raw_out.write_bytes(b"fake-raw")
        final_out.write_bytes(b"fake-final")
        return DecropReport(source_size=(1, 1), raw_size=(1, 1), final_size=(1, 1), resized=False)

    with (
        patch("pancratius.translation.image.translator.generate_image_translation", return_value=_FakeGenerationResponse()),
        patch("pancratius.translation.image.translator.vision_text", side_effect=fake_vision_text),
        patch("pancratius.translation.image.translator.decrop_to_source", side_effect=fake_decrop),
    ):
        result = translate_image(job, ImageTranslationConfig(max_attempts=3, inter_call_sleep=0.0), "fake-key")

    assert not result.ok
    assert len(result.attempts) == 3
    assert result.error is not None
    assert "QA failed after" in result.error
    assert not job.target_image.exists()


def test_pipeline_embedded_only_leftover_accepted_with_caveat(tmp_path: Path) -> None:
    from pancratius.translation.image.decrop import DecropReport
    from pancratius.translation.image.translator import translate_image

    job = _job_with_caveat_policy(tmp_path)

    def fake_vision_text(
        *,
        images: list[Path],  # noqa: ARG001
        prompt: str,
        api_key: str,  # noqa: ARG001
        response_format: object = None,  # noqa: ARG001
    ) -> _FakeVisionResponse:
        if "quality assurance" in prompt:
            return _FakeVisionResponse(text=_make_qa_json("fail", ["source_text_left"], embedded=True))
        return _FakeVisionResponse(text=_make_recon_json())

    def fake_decrop(
        raw_bytes: bytes,  # noqa: ARG001
        source: Path,  # noqa: ARG001
        raw_out: Path,
        final_out: Path,
    ) -> DecropReport:
        raw_out.parent.mkdir(parents=True, exist_ok=True)
        raw_out.write_bytes(b"fake-raw")
        final_out.write_bytes(b"fake-final")
        return DecropReport(source_size=(1, 1), raw_size=(1, 1), final_size=(1, 1), resized=False)

    with (
        patch("pancratius.translation.image.translator.generate_image_translation", return_value=_FakeGenerationResponse()),
        patch("pancratius.translation.image.translator.vision_text", side_effect=fake_vision_text),
        patch("pancratius.translation.image.translator.decrop_to_source", side_effect=fake_decrop),
    ):
        result = translate_image(job, ImageTranslationConfig(max_attempts=2, inter_call_sleep=0.0), "fake-key")

    assert result.status is ImageTranslationStatus.OK_WITH_CAVEAT
    assert result.ok
    assert job.target_image.exists()
    assert result.embedded_leftovers == ("defect: source_text_left",)


def test_pipeline_embedded_only_leftover_hard_fails_without_provider_policy(tmp_path: Path) -> None:
    from pancratius.translation.image.decrop import DecropReport
    from pancratius.translation.image.translator import translate_image

    job = _job(tmp_path)

    def fake_vision_text(
        *,
        images: list[Path],  # noqa: ARG001
        prompt: str,
        api_key: str,  # noqa: ARG001
        response_format: object = None,  # noqa: ARG001
    ) -> _FakeVisionResponse:
        if "quality assurance" in prompt:
            return _FakeVisionResponse(text=_make_qa_json("fail", ["source_text_left"], embedded=True))
        return _FakeVisionResponse(text=_make_recon_json())

    def fake_decrop(
        raw_bytes: bytes,  # noqa: ARG001
        source: Path,  # noqa: ARG001
        raw_out: Path,
        final_out: Path,
    ) -> DecropReport:
        raw_out.parent.mkdir(parents=True, exist_ok=True)
        raw_out.write_bytes(b"fake-raw")
        final_out.write_bytes(b"fake-final")
        return DecropReport(source_size=(1, 1), raw_size=(1, 1), final_size=(1, 1), resized=False)

    with (
        patch("pancratius.translation.image.translator.generate_image_translation", return_value=_FakeGenerationResponse()),
        patch("pancratius.translation.image.translator.vision_text", side_effect=fake_vision_text),
        patch("pancratius.translation.image.translator.decrop_to_source", side_effect=fake_decrop),
    ):
        result = translate_image(job, ImageTranslationConfig(max_attempts=1, inter_call_sleep=0.0), "fake-key")

    assert result.status is ImageTranslationStatus.FAIL
    assert not job.target_image.exists()


def test_pipeline_insufficient_credits_is_terminal(tmp_path: Path) -> None:
    from pancratius.translation.image.client import InsufficientCreditsError
    from pancratius.translation.image.translator import translate_image

    job = _job(tmp_path)

    with (
        patch(
            "pancratius.translation.image.translator.vision_text",
            side_effect=InsufficientCreditsError("HTTP 402: no credits"),
        ),
        pytest.raises(InsufficientCreditsError),
    ):
        translate_image(job, ImageTranslationConfig(max_attempts=1, inter_call_sleep=0.0), "fake-key")


def test_client_recognizes_non_402_insufficient_credit_body() -> None:
    from pancratius.translation.image.client import _looks_like_insufficient_credits

    assert _looks_like_insufficient_credits(400, '{"error":"can only afford 18000 tokens"}')
    assert _looks_like_insufficient_credits(200, '{"error":{"message":"Insufficient balance"}}')


def test_config_rejects_zero_max_attempts() -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        ImageTranslationConfig(max_attempts=0)


def test_decrop_trims_white_border_before_resize(tmp_path: Path) -> None:
    from pancratius.translation.image.decrop import decrop_to_source

    source = tmp_path / "source.png"
    Image.new("RGB", (2, 2), (200, 0, 0)).save(source)
    raw = Image.new("RGB", (4, 4), (255, 255, 255))
    for x in (1, 2):
        for y in (1, 2):
            raw.putpixel((x, y), (200, 0, 0))
    buf = BytesIO()
    raw.save(buf, format="PNG")

    final = tmp_path / "final.png"
    report = decrop_to_source(
        raw_bytes=buf.getvalue(),
        source=source,
        raw_out=tmp_path / "raw.png",
        final_out=final,
    )

    assert report.resized
    with Image.open(final) as out:
        assert out.size == (2, 2)
        assert out.getpixel((0, 0)) == (200, 0, 0)
