"""Unit tests for the cover-translation pipeline logic.

Tests pure functions and a fake-client integration harness — no network calls.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

from pancratius.cover.models import (
    AUTHOR_EN,
    CoverElement,
    ElementRole,
    QaDiscrepancy,
    QaVerdict,
    ResolvedElement,
    ResolvedTitle,
    TitlePin,
    TitleSource,
)
from pancratius.cover.pipeline import CoverTranslateConfig
from pancratius.cover.prompts import SteeringLevel, build_steering, generation_prompt, qa_prompt
from pancratius.cover.schema import _extract_json, parse_qa, parse_recon, qa_format, recon_format
from pancratius.cover.seed import (
    SeedMap,
    author_only_elements,
    init_seed,
    load_seed,
    plan_title,
    resolve_elements,
    resolve_pin,
    resolve_title,
)


def _pinned(to_render: str, *, wording: str = "", source: TitleSource = TitleSource.EN_MD) -> ResolvedTitle:
    """A pinned ResolvedTitle for prompt tests (wording defaults to to_render)."""
    return ResolvedTitle(to_render=to_render, authoritative_wording=wording or to_render, source=source)


_NO_TITLE = ResolvedTitle(to_render="", authoritative_wording="", source=TitleSource.MODEL)


def _element(
    russian: str,
    english: str,
    *,
    role: ElementRole = ElementRole.OTHER,
    art_baked: bool = False,
) -> ResolvedElement:
    """A ResolvedElement for prompt/steering tests."""
    return ResolvedElement(role=role, russian=russian, english=english, art_baked=art_baked)

# ---------------------------------------------------------------------------
# Title resolution
# ---------------------------------------------------------------------------


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


def test_resolve_pin_falls_back_to_seed(tmp_path: Path) -> None:
    seed = SeedMap(titles={"book-50": "Mammon"}, overrides={})
    pin = resolve_pin("book-50", books_root=tmp_path / "no-such-dir", queue_titles={}, seed=seed)
    assert pin == TitlePin(wording="Mammon", source=TitleSource.SEED)


def test_resolve_pin_falls_back_to_queue(tmp_path: Path) -> None:
    seed = SeedMap(titles={}, overrides={})
    pin = resolve_pin(
        "book-51", books_root=tmp_path / "no-such-dir", queue_titles={"book-51": "The Path"}, seed=seed
    )
    assert pin == TitlePin(wording="The Path", source=TitleSource.QUEUE)


def test_resolve_pin_returns_none_when_no_pin(tmp_path: Path) -> None:
    seed = SeedMap(titles={}, overrides={})
    pin = resolve_pin("book-99", books_root=tmp_path / "no-such-dir", queue_titles={}, seed=seed)
    assert pin is None


def test_resolve_title_unquotes_single_quoted_yaml(tmp_path: Path) -> None:
    books = tmp_path / "books" / "01-test"
    books.mkdir(parents=True)
    (books / "en.md").write_text(
        "---\ntitle: 'Gospel of the One: I Am'\nlang: en\n---\n",
        encoding="utf-8",
    )
    seed = SeedMap(titles={}, overrides={})
    title = resolve_title("book-01", books_root=tmp_path / "books", queue_titles={}, seed=seed)
    # The pin's authoritative wording is the full catalogue title…
    assert title.authoritative_wording == "Gospel of the One: I Am"
    # …but the cover renders only the short form before the colon.
    assert title.to_render == "Gospel of the One"
    assert title.source == TitleSource.EN_MD


# ---------------------------------------------------------------------------
# Title PLAN: deriving the single cover-rendered title (book-50 case)
# ---------------------------------------------------------------------------


def test_plan_title_renders_short_form_before_colon() -> None:
    # Book-50: long catalogue pin, displayed cover title is the short head.
    pin = TitlePin(
        wording="Mammon: Why You Are in His Power and How to Step into the Light",
        source=TitleSource.EN_MD,
    )
    plan = plan_title(pin)
    assert plan.to_render == "Mammon"  # the displayed (short) form
    assert plan.authoritative_wording == pin.wording  # full wording retained
    assert plan.source == TitleSource.EN_MD
    assert plan.is_pinned


def test_plan_title_no_colon_renders_whole_pin() -> None:
    pin = TitlePin(wording="The Book of Love", source=TitleSource.EN_MD)
    plan = plan_title(pin)
    assert plan.to_render == "The Book of Love"


def test_plan_title_no_pin_is_model_translated() -> None:
    plan = plan_title(None)
    assert plan.to_render == ""
    assert plan.authoritative_wording == ""
    assert plan.source == TitleSource.MODEL
    assert not plan.is_pinned


# ---------------------------------------------------------------------------
# Element-English resolution: override > pin(for title) > author > recon
# ---------------------------------------------------------------------------


def _recon_element(
    russian: str,
    english: str,
    *,
    role: ElementRole = ElementRole.OTHER,
    art_baked: bool = False,
) -> CoverElement:
    return CoverElement(role=role, russian=russian, english=english, art_baked=art_baked)


def test_resolve_elements_pin_wins_for_title_over_recon() -> None:
    # The title pin ("Mammon") beats the recon model's own translation.
    title = _pinned("Mammon")
    elements = resolve_elements(
        [_recon_element("Мамона", "Money", role=ElementRole.TITLE)],
        title=title,
        overrides={},
    )
    assert elements[0].english == "Mammon"


def test_resolve_elements_no_pin_keeps_recon_translation_for_title() -> None:
    elements = resolve_elements(
        [_recon_element("Мамона", "Mammon (recon)", role=ElementRole.TITLE)],
        title=_NO_TITLE,
        overrides={},
    )
    assert elements[0].english == "Mammon (recon)"


def test_resolve_elements_author_is_fixed_string() -> None:
    elements = resolve_elements(
        [_recon_element("Сергей Панкратиус", "Sergey Pankratius", role=ElementRole.AUTHOR)],
        title=_NO_TITLE,
        overrides={},
    )
    assert elements[0].english == AUTHOR_EN


def test_resolve_elements_override_wins_over_everything() -> None:
    # An override keyed on the exact Russian beats the pin and the author rule.
    title = _pinned("Mammon")
    elements = resolve_elements(
        [
            _recon_element("Мамона", "Money", role=ElementRole.TITLE),
            _recon_element("Сергей Панкратиус", "X", role=ElementRole.AUTHOR),
        ],
        title=title,
        overrides={"Мамона": "MAMMON-OVERRIDE", "Сергей Панкратиус": "Author-Override"},
    )
    assert elements[0].english == "MAMMON-OVERRIDE"
    assert elements[1].english == "Author-Override"


def test_resolve_elements_recon_translation_for_tagline() -> None:
    # The «в его власти» tagline that used to be MISSED: recon translates it,
    # resolution keeps that English, and it ends up in the map.
    elements = resolve_elements(
        [_recon_element("в его власти", "In His Power", role=ElementRole.TAGLINE)],
        title=_NO_TITLE,
        overrides={},
    )
    assert elements[0].russian == "в его власти"
    assert elements[0].english == "In His Power"


def test_resolve_elements_drops_blank_russian() -> None:
    elements = resolve_elements(
        [
            _recon_element("   ", "noise", role=ElementRole.OTHER),
            _recon_element("Мамона", "Mammon", role=ElementRole.TITLE),
        ],
        title=_NO_TITLE,
        overrides={},
    )
    assert [e.russian for e in elements] == ["Мамона"]


def test_resolve_elements_preserves_art_baked_flag() -> None:
    elements = resolve_elements(
        [_recon_element("Система дефицита", "System of Scarcity", role=ElementRole.ART_TEXT, art_baked=True)],
        title=_NO_TITLE,
        overrides={},
    )
    assert elements[0].art_baked is True


def test_author_only_elements_pins_author() -> None:
    # The recon-failure fallback still pins the author rather than self-translating.
    elements = author_only_elements()
    assert len(elements) == 1
    assert elements[0].role is ElementRole.AUTHOR
    assert elements[0].english == AUTHOR_EN


# ---------------------------------------------------------------------------
# Seed loading
# ---------------------------------------------------------------------------


def test_load_seed_returns_empty_when_absent(tmp_path: Path) -> None:
    # Pure read — must NOT create the file.
    seed_path = tmp_path / "seed.json"
    seed = load_seed(seed_path)
    assert seed.titles == {}
    assert seed.overrides == {}
    assert not seed_path.exists()


def test_init_seed_creates_template(tmp_path: Path) -> None:
    seed_path = tmp_path / "seed.json"
    init_seed(seed_path)
    assert seed_path.exists()
    raw = json.loads(seed_path.read_text())
    assert "titles" in raw
    assert "overrides" in raw


def test_init_seed_is_idempotent(tmp_path: Path) -> None:
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(json.dumps({"titles": {"book-01": "X"}, "overrides": {}}))
    init_seed(seed_path)
    # Existing file must not be overwritten
    raw = json.loads(seed_path.read_text())
    assert raw["titles"] == {"book-01": "X"}


def test_load_seed_reads_existing_file(tmp_path: Path) -> None:
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(
        json.dumps({"titles": {"book-50": "Mammon"}, "overrides": {"Панкратиус": "Pancratius"}}),
        encoding="utf-8",
    )
    seed = load_seed(seed_path)
    assert seed.titles == {"book-50": "Mammon"}
    assert seed.overrides == {"Панкратиус": "Pancratius"}


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------


def test_generation_prompt_has_explicit_per_element_map_not_translate_all() -> None:
    # The core fix: the prompt enumerates each replacement, never "translate all
    # Russian yourself". The model renders given strings; it cannot miss one.
    elements = [
        _element("Мамона", "Mammon", role=ElementRole.TITLE),
        _element("в его власти", "In His Power", role=ElementRole.TAGLINE),
        _element("Сергей Панкратиус", "Sergei Pancratius", role=ElementRole.AUTHOR),
    ]
    prompt = generation_prompt(elements, steering="")
    # One explicit replacement line per element, pairing the exact RU with the EN.
    assert "Replace «Мамона» with «Mammon»" in prompt
    assert "Replace «в его власти» with «In His Power»" in prompt
    assert "Replace «Сергей Панкратиус» with «Sergei Pancratius»" in prompt
    # The old fused instruction must be gone — generation no longer self-translates.
    assert "Translate all other Russian text faithfully yourself" not in prompt
    assert "Translate every Russian text element" not in prompt
    # Layout/artwork must be preserved.
    assert "pixel-identical" in prompt or "identical" in prompt


def test_generation_prompt_flags_art_baked_element() -> None:
    elements = [_element("Система дефицита", "System of Scarcity", role=ElementRole.ART_TEXT, art_baked=True)]
    prompt = generation_prompt(elements, steering="")
    # Art-baked elements are named explicitly with both source and translation
    assert "Система дефицита" in prompt
    assert "System of Scarcity" in prompt
    # Emphatic instruction must use seal/emblem/coin/banner vocabulary
    assert any(word in prompt for word in ("seal", "emblem", "coin", "banner"))


def test_generation_prompt_appends_steering() -> None:
    steering = "Correction needed:\n- Some Russian text was left untranslated"
    prompt = generation_prompt([_element("Мамона", "Mammon")], steering=steering)
    assert steering in prompt


def test_generation_prompt_empty_elements_has_no_map_block() -> None:
    prompt = generation_prompt([], steering="")
    assert "Replace «" not in prompt


def test_qa_prompt_includes_author_requirement() -> None:
    prompt = qa_prompt(_NO_TITLE)
    assert "Sergei Pancratius" in prompt
    assert "Cyrillic" in prompt or "cyrillic" in prompt.lower()


def test_build_steering_names_elements_by_english_not_cyrillic() -> None:
    # Steering references the specific failing element by its resolved ENGLISH,
    # never the raw QA description (which may carry Cyrillic that trips filters).
    discrepancies = [
        QaDiscrepancy(kind="cyrillic_left", description="«Система дефицита» is still in Russian"),
        QaDiscrepancy(kind="author_wrong", description="Author rendered as 'Pankratius'"),
    ]
    elements = [
        _element("Система дефицита", "System of Scarcity", role=ElementRole.ART_TEXT, art_baked=True),
        _element("Мамона", "Mammon", role=ElementRole.TITLE),
    ]
    steering = build_steering(discrepancies, elements=elements)
    # Names the specific element by its English target.
    assert "System of Scarcity" in steering
    assert "author" in steering.lower() or "Pancratius" in steering
    # Must NOT leak raw QA descriptions / source Cyrillic / wrong transliteration.
    assert "Система дефицита" not in steering
    assert "Pankratius" not in steering


def test_build_steering_orders_art_baked_first() -> None:
    discrepancies = [QaDiscrepancy(kind="cyrillic_left", description="leftover")]
    elements = [
        _element("Мамона", "Mammon", role=ElementRole.TITLE, art_baked=False),
        _element("Система дефицита", "System of Scarcity", role=ElementRole.ART_TEXT, art_baked=True),
    ]
    steering = build_steering(discrepancies, elements=elements)
    # Art-baked element (the usual leftover culprit) is named before the overlay one.
    assert steering.index("System of Scarcity") < steering.index("Mammon")


def test_build_steering_empty_on_no_discrepancies() -> None:
    assert build_steering([], elements=[_element("Мамона", "Mammon")]) == ""


# ---------------------------------------------------------------------------
# Schema: recon
# ---------------------------------------------------------------------------


def test_recon_format_is_strict_json_schema() -> None:
    fmt = recon_format()
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    schema = fmt["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert "elements" in schema["properties"]
    assert "displayed_title" in schema["properties"]


def test_parse_recon_extracts_russian_english_role_and_artbaked() -> None:
    text = json.dumps({
        "elements": [
            {"role": "title", "russian": "Мамона", "english": "Mammon", "art_baked": False},
            {"role": "tagline", "russian": "в его власти", "english": "In His Power", "art_baked": False},
            {"role": "art_text", "russian": "Система дефицита", "english": "System of Scarcity", "art_baked": True},
        ],
        "displayed_title": "Мамона",
    })
    recon = parse_recon(text)
    assert [(e.role, e.russian, e.english, e.art_baked) for e in recon.elements] == [
        (ElementRole.TITLE, "Мамона", "Mammon", False),
        (ElementRole.TAGLINE, "в его власти", "In His Power", False),
        (ElementRole.ART_TEXT, "Система дефицита", "System of Scarcity", True),
    ]
    assert recon.displayed_title == "Мамона"
    assert recon.raw_json == text


def test_parse_recon_defaults_art_baked_false_when_absent() -> None:
    text = json.dumps({
        "elements": [{"role": "title", "russian": "Мамона", "english": "Mammon"}],
        "displayed_title": "Мамона",
    })
    recon = parse_recon(text)
    assert recon.elements[0].art_baked is False


def test_parse_recon_coerces_unknown_role_to_other() -> None:
    text = json.dumps({
        "elements": [{"role": "banner", "russian": "X", "english": "Y", "art_baked": False}],
        "displayed_title": "",
    })
    recon = parse_recon(text)
    assert recon.elements[0].role is ElementRole.OTHER


def test_parse_recon_skips_elements_missing_russian_or_english() -> None:
    text = json.dumps({
        "elements": [
            {"role": "title", "russian": "Мамона", "english": "Mammon", "art_baked": False},
            {"role": "tagline", "russian": "в его власти"},   # missing english
            {"role": "author", "english": "Sergei Pancratius"},  # missing russian
        ],
        "displayed_title": "Мамона",
    })
    recon = parse_recon(text)
    assert [(e.russian, e.english) for e in recon.elements] == [("Мамона", "Mammon")]


# ---------------------------------------------------------------------------
# Schema: QA
# ---------------------------------------------------------------------------


def test_qa_format_is_strict_json_schema() -> None:
    fmt = qa_format()
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    schema = fmt["json_schema"]["schema"]
    assert "verdict" in schema["properties"]
    assert "discrepancies" in schema["properties"]


def test_parse_qa_pass_returns_empty_discrepancies() -> None:
    text = json.dumps({"verdict": "pass", "discrepancies": []})
    qa = parse_qa(text)
    assert qa.verdict == QaVerdict.PASS
    assert qa.discrepancies == ()


def test_parse_qa_fail_returns_discrepancies() -> None:
    text = json.dumps({
        "verdict": "fail",
        "discrepancies": [
            {"kind": "cyrillic_left", "description": "Title still reads «Мамона»"},
            {"kind": "author_wrong", "description": "Author rendered as Pankratius"},
        ],
    })
    qa = parse_qa(text)
    assert qa.verdict == QaVerdict.FAIL
    assert qa.discrepancies == (
        QaDiscrepancy(kind="cyrillic_left", description="Title still reads «Мамона»"),
        QaDiscrepancy(kind="author_wrong", description="Author rendered as Pankratius"),
    )


def test_parse_qa_unknown_verdict_fails_closed() -> None:
    # Anything other than the literal "pass" is FAIL — never infer PASS loosely.
    qa = parse_qa(json.dumps({"verdict": "passed-ish", "discrepancies": []}))
    assert qa.verdict == QaVerdict.FAIL


def test_parse_qa_skips_malformed_discrepancies() -> None:
    text = json.dumps({
        "verdict": "fail",
        "discrepancies": [
            {"kind": "cyrillic_left", "description": "OK"},
            {"description": "missing kind"},
        ],
    })
    qa = parse_qa(text)
    assert qa.discrepancies == (QaDiscrepancy(kind="cyrillic_left", description="OK"),)


# ---------------------------------------------------------------------------
# QaVerdict enum
# ---------------------------------------------------------------------------


def test_qa_verdict_pass_value() -> None:
    assert QaVerdict.PASS == "pass"
    assert QaVerdict.FAIL == "fail"


# ---------------------------------------------------------------------------
# _extract_json: balanced-object scanning (H2)
# ---------------------------------------------------------------------------


def test_extract_json_plain_object() -> None:
    text = '{"verdict": "pass", "discrepancies": []}'
    assert json.loads(_extract_json(text)) == {"verdict": "pass", "discrepancies": []}


def test_extract_json_fenced() -> None:
    text = '```json\n{"verdict": "fail"}\n```'
    assert json.loads(_extract_json(text))["verdict"] == "fail"


def test_extract_json_extra_braces_after() -> None:
    # Greedy {.*} would grab the trailing brace; raw_decode stops at first balanced close
    text = 'Here is the result: {"verdict": "pass", "discrepancies": []} extra {stuff}'
    result = json.loads(_extract_json(text))
    assert result["verdict"] == "pass"


def test_extract_json_leading_text() -> None:
    text = 'The analysis:\n{"verdict": "fail", "discrepancies": [{"kind": "cyrillic_left", "description": "x"}]}'
    result = json.loads(_extract_json(text))
    assert result["verdict"] == "fail"
    assert len(result["discrepancies"]) == 1


# ---------------------------------------------------------------------------
# QA parse-failure FAILS CLOSED (H3)
# ---------------------------------------------------------------------------


def test_parse_qa_fail_on_unparseable_response() -> None:
    # Garbage text that contains "verdict": "pass" as a substring must still raise.
    import pytest
    junk = 'I cannot parse this. A good response would have "verdict": "pass" but I see errors.'
    with pytest.raises(json.JSONDecodeError):
        parse_qa(junk)


def test_qa_parse_failure_not_inferred_as_pass() -> None:
    # The schema layer raises on a non-object reply; the pipeline then FAILs closed.
    import pytest
    with pytest.raises((json.JSONDecodeError, ValueError)):
        parse_qa('"verdict": "pass"')  # not wrapped in an object


# ---------------------------------------------------------------------------
# H1 (central): a long pin + short displayed title resolves to the SHORT form,
# and the prompt pins exactly that — no full-vs-displayed contradiction.
# ---------------------------------------------------------------------------


def test_resolved_title_is_short_form_when_long_pin_disagrees(tmp_path: Path) -> None:
    # Book-50: en.md catalogue title is long; the cover shows just "Mammon".
    books = tmp_path / "books" / "50-mamona"
    books.mkdir(parents=True)
    (books / "en.md").write_text(
        "---\ntitle: 'Mammon: Why You Are in His Power and How to Step into the Light'\n---\n",
        encoding="utf-8",
    )
    seed = SeedMap(titles={}, overrides={})
    title = resolve_title("book-50", books_root=tmp_path / "books", queue_titles={}, seed=seed)

    # The ONE value to render is the short displayed form, not the catalogue title.
    assert title.to_render == "Mammon"
    assert title.authoritative_wording.startswith("Mammon: Why")

    # The title's resolved element English is the short pin; the generation map
    # renders exactly "Mammon" and never the full catalogue title.
    elements = resolve_elements(
        [CoverElement(role=ElementRole.TITLE, russian="Мамона", english="Money", art_baked=False)],
        title=title,
        overrides={},
    )
    prompt = generation_prompt(elements, steering="")
    assert "Replace «Мамона» with «Mammon»" in prompt  # pin wins over recon's "Money"
    assert "Why You Are in His Power" not in prompt

    # QA anchors on the same short string.
    qa = qa_prompt(title)
    assert "The title should read exactly 'Mammon'." in qa
    assert "Why You Are in His Power" not in qa


def test_qa_prompt_no_pin_omits_title_assertion() -> None:
    # No pin → QA must not assert any title wording (model translated it faithfully).
    qa = qa_prompt(_NO_TITLE)
    assert "should read exactly" not in qa


# ---------------------------------------------------------------------------
# M2: build_steering escalation on repeated defect (SteeringLevel)
# ---------------------------------------------------------------------------


def test_build_steering_escalates_to_urgent() -> None:
    discrepancies = [QaDiscrepancy(kind="cyrillic_left", description="still Russian text")]
    firm = build_steering(discrepancies, level=SteeringLevel.FIRM)
    urgent = build_steering(discrepancies, level=SteeringLevel.URGENT)
    assert firm != urgent
    assert "CRITICAL" in urgent


def test_build_steering_firm_not_critical() -> None:
    discrepancies = [QaDiscrepancy(kind="cyrillic_left", description="still Russian text")]
    steering = build_steering(discrepancies, level=SteeringLevel.FIRM)
    assert "CRITICAL" not in steering


# ---------------------------------------------------------------------------
# Config invariants
# ---------------------------------------------------------------------------


def test_config_rejects_zero_max_attempts(tmp_path: Path) -> None:
    import pytest
    with pytest.raises(ValueError, match="max_attempts"):
        CoverTranslateConfig(output_dir=tmp_path / "out", max_attempts=0)


def test_config_rejects_negative_sleep(tmp_path: Path) -> None:
    import pytest
    with pytest.raises(ValueError, match="inter_call_sleep"):
        CoverTranslateConfig(output_dir=tmp_path / "out", inter_call_sleep=-1.0)


# ---------------------------------------------------------------------------
# Pipeline integration with fake client (a–c)
# ---------------------------------------------------------------------------

@dataclass
class _FakeVisionResponse:
    text: str
    cost_usd: float = 0.001
    usage: dict[str, object] = field(default_factory=dict)


@dataclass
class _FakeGenerationResponse:
    image_bytes: bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    cost_usd: float = 0.068
    usage: dict[str, object] = field(default_factory=dict)


def _make_recon_json(displayed: str = "Мамона") -> str:
    return json.dumps({
        "elements": [
            {"role": "title", "russian": displayed, "english": "Mammon", "art_baked": False},
            {"role": "author", "russian": "Сергей Панкратиус", "english": "Sergei Pancratius", "art_baked": False},
        ],
        "displayed_title": displayed,
    })


def _make_qa_json(verdict: str, kinds: list[str] | None = None) -> str:
    discs = [{"kind": k, "description": f"defect: {k}"} for k in (kinds or [])]
    return json.dumps({"verdict": verdict, "discrepancies": discs})


def _make_config(tmp_path: Path) -> CoverTranslateConfig:
    covers_dir = tmp_path / "covers"
    covers_dir.mkdir()
    # Create a minimal valid PNG (8-byte header + 4-byte IEND)
    png_bytes = (
        b"\x89PNG\r\n\x1a\n"                    # PNG signature
        b"\x00\x00\x00\rIHDR"                   # IHDR chunk length + type
        b"\x00\x00\x00\x01"                     # width = 1
        b"\x00\x00\x00\x01"                     # height = 1
        b"\x08\x02"                             # bit depth 8, color type RGB
        b"\x00\x00\x00"                         # compression, filter, interlace
        b"\x90wS\xde"                           # CRC
        b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"  # IDAT
        b"\x00\x00\x00\x00IEND\xaeB`\x82"       # IEND
    )
    (covers_dir / "book-50.ru.png").write_bytes(png_bytes)
    return CoverTranslateConfig(
        output_dir=tmp_path / "out",
        covers_dir=covers_dir,
        queue_md=tmp_path / "QUEUE.md",
        books_root=tmp_path / "books",
        seed_path=tmp_path / "seed.json",
        max_attempts=3,
        inter_call_sleep=0.0,
    )


def test_pipeline_pass_on_attempt_n_stops_loop(tmp_path: Path) -> None:
    """(a) PASS on attempt 2 stops the loop; exactly 2 generation+QA cycles run."""
    # No pre-existing en.png so we go straight into the generation loop.
    config = _make_config(tmp_path)

    qa_responses = [
        _make_qa_json("fail", ["cyrillic_left"]),  # attempt 1 → fail
        _make_qa_json("pass"),                       # attempt 2 → pass
    ]
    qa_iter = iter(qa_responses)

    def fake_vision_text(
        *,
        images: list[Path],  # noqa: ARG001
        prompt: str,
        api_key: str,  # noqa: ARG001
        response_format: object = None,  # noqa: ARG001
    ) -> _FakeVisionResponse:
        if "quality assurance" in prompt:
            return _FakeVisionResponse(text=next(qa_iter))
        return _FakeVisionResponse(text=_make_recon_json())

    from pancratius.cover.decrop import DecropReport

    decrop_report = DecropReport(source_size=(1, 1), raw_size=(1, 1), final_size=(1, 1), resized=False)

    def fake_decrop(
        raw_bytes: bytes,  # noqa: ARG001
        source: Path,  # noqa: ARG001
        raw_out: Path,
        final_out: Path,
    ) -> DecropReport:
        raw_out.parent.mkdir(parents=True, exist_ok=True)
        raw_out.write_bytes(b"fake-raw")
        final_out.write_bytes(b"fake-final")
        return decrop_report

    with (
        patch("pancratius.cover.pipeline.generate_cover", return_value=_FakeGenerationResponse()),
        patch("pancratius.cover.pipeline.vision_text", side_effect=fake_vision_text),
        patch("pancratius.cover.pipeline.decrop_to_source", side_effect=fake_decrop),
    ):
        from pancratius.cover.pipeline import translate_cover
        result = translate_cover("book-50", config, "fake-key")

    assert result.ok
    assert len(result.attempts) == 2
    assert result.attempts[-1].qa.verdict == QaVerdict.PASS


def test_pipeline_persistent_fail_exhausts_attempts(tmp_path: Path) -> None:
    """(b) Persistent FAIL exhausts exactly max_attempts and reports unresolved."""
    config = _make_config(tmp_path)

    def fake_vision_text(
        *,
        images: list[Path],  # noqa: ARG001
        prompt: str,
        api_key: str,  # noqa: ARG001
        response_format: object = None,  # noqa: ARG001
    ) -> _FakeVisionResponse:
        if "quality assurance" in prompt:
            return _FakeVisionResponse(text=_make_qa_json("fail", ["cyrillic_left"]))
        return _FakeVisionResponse(text=_make_recon_json())

    from pancratius.cover.decrop import DecropReport

    decrop_report = DecropReport(source_size=(1, 1), raw_size=(1, 1), final_size=(1, 1), resized=False)

    def fake_decrop(
        raw_bytes: bytes,  # noqa: ARG001
        source: Path,  # noqa: ARG001
        raw_out: Path,
        final_out: Path,
    ) -> DecropReport:
        raw_out.parent.mkdir(parents=True, exist_ok=True)
        raw_out.write_bytes(b"fake-raw")
        final_out.write_bytes(b"fake-final")
        return decrop_report

    with (
        patch("pancratius.cover.pipeline.generate_cover", return_value=_FakeGenerationResponse()),
        patch("pancratius.cover.pipeline.vision_text", side_effect=fake_vision_text),
        patch("pancratius.cover.pipeline.decrop_to_source", side_effect=fake_decrop),
    ):
        from pancratius.cover.pipeline import translate_cover
        result = translate_cover("book-50", config, "fake-key")

    assert not result.ok
    assert len(result.attempts) == config.max_attempts
    assert result.error is not None
    assert "QA failed after" in result.error


def test_pipeline_retry_edits_previous_output_not_raw_source(tmp_path: Path) -> None:
    """Degradation fix: attempt 1 edits the RU source; a retry edits the previous
    attempt's EN output (a targeted touch-up), not the raw source re-interpreted."""
    config = _make_config(tmp_path)
    source = config.covers_dir / "book-50.ru.png"

    edit_bases: list[Path] = []

    def fake_generate(src: Path, prompt: str, api_key: str, *, model: str | None = None) -> _FakeGenerationResponse:  # noqa: ARG001
        edit_bases.append(src)
        return _FakeGenerationResponse()

    qa_responses = iter([
        _make_qa_json("fail", ["cyrillic_left"]),  # attempt 1 → fail
        _make_qa_json("pass"),                       # attempt 2 → pass
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

    from pancratius.cover.decrop import DecropReport

    decrop_report = DecropReport(source_size=(1, 1), raw_size=(1, 1), final_size=(1, 1), resized=False)

    def fake_decrop(
        raw_bytes: bytes,  # noqa: ARG001
        source: Path,  # noqa: ARG001
        raw_out: Path,
        final_out: Path,
    ) -> DecropReport:
        raw_out.parent.mkdir(parents=True, exist_ok=True)
        raw_out.write_bytes(b"fake-raw")
        final_out.write_bytes(b"fake-final")
        return decrop_report

    with (
        patch("pancratius.cover.pipeline.generate_cover", side_effect=fake_generate),
        patch("pancratius.cover.pipeline.vision_text", side_effect=fake_vision_text),
        patch("pancratius.cover.pipeline.decrop_to_source", side_effect=fake_decrop),
    ):
        from pancratius.cover.pipeline import translate_cover
        result = translate_cover("book-50", config, "fake-key")

    assert result.ok
    assert len(edit_bases) == 2
    # Attempt 1 edits the RU source; the retry edits the previous attempt's output
    # (a .prev.png snapshot), NOT the raw source again.
    assert edit_bases[0] == source
    assert edit_bases[1] != source
    assert edit_bases[1].name == "book-50.prev.png"
    # The retry scratch file must not persist past the run.
    assert not (config.output_dir / "book-50.prev.png").exists()


def test_pipeline_generation_error_caught_per_cover(tmp_path: Path) -> None:
    """(c) A generation error mid-batch is caught; the batch result is a FAIL entry,
    not an unhandled exception that kills the caller."""
    from pancratius.cover.client import CoverClientError
    from pancratius.cover.pipeline import translate_covers

    config = _make_config(tmp_path)

    def fake_vision_text(
        *,
        images: list[Path],  # noqa: ARG001
        prompt: str,  # noqa: ARG001
        api_key: str,  # noqa: ARG001
        response_format: object = None,  # noqa: ARG001
    ) -> _FakeVisionResponse:
        return _FakeVisionResponse(text=_make_recon_json())

    with (
        patch("pancratius.cover.pipeline.generate_cover",
              side_effect=CoverClientError("bad image bytes from generation: test")),
        patch("pancratius.cover.pipeline.vision_text", side_effect=fake_vision_text),
        patch("pancratius.cover.pipeline.api_key_from_env", return_value="fake-key"),
    ):
        results = translate_covers(["book-50"], config)

    assert len(results) == 1
    assert not results[0].ok
    # translate_covers broad-catches exceptions — no unhandled exception


# ---------------------------------------------------------------------------
# Refinement 1a: art-baked-only leftover → ACCEPTED with caveat, file kept
# ---------------------------------------------------------------------------


def _make_recon_json_with_emblem(displayed: str = "Мамона") -> str:
    return json.dumps({
        "elements": [
            {"role": "title", "russian": displayed, "english": "Mammon", "art_baked": False},
            {"role": "author", "russian": "Сергей Панкратиус", "english": "Sergei Pancratius", "art_baked": False},
            {"role": "art_text", "russian": "Система дефицита", "english": "System of Scarcity", "art_baked": True},
        ],
        "displayed_title": displayed,
    })


def test_art_baked_only_leftover_accepted_with_caveat(tmp_path: Path) -> None:
    """After cap, if ALL remaining discrepancies are art_baked elements, the cover
    is kept (file NOT unlinked) and result is ok_with_caveat, not a hard fail."""
    from pancratius.cover.decrop import DecropReport
    from pancratius.cover.models import CoverStatus

    config = _make_config(tmp_path)
    # All 3 attempts fail; the discrepancy kind is cyrillic_left but it refers to
    # the art_baked element (Система дефицита / System of Scarcity).
    # We simulate: the pipeline has an art_baked element in its resolved set, and
    # the final QA reports exactly one cyrillic_left discrepancy that the pipeline
    # can classify as art_baked.
    # The test relies on the pipeline matching unresolved discrepancies against
    # art_baked elements by their english text.

    qa_fail_art_baked = json.dumps({
        "verdict": "fail",
        "discrepancies": [
            {
                "kind": "cyrillic_left",
                "description": "Emblem text «Система дефицита» still in Russian",
                "in_artwork": True,
            },
        ],
    })

    def fake_vision_text(
        *,
        images: list[Path],  # noqa: ARG001
        prompt: str,
        api_key: str,  # noqa: ARG001
        response_format: object = None,  # noqa: ARG001
    ) -> _FakeVisionResponse:
        if "quality assurance" in prompt:
            return _FakeVisionResponse(text=qa_fail_art_baked)
        return _FakeVisionResponse(text=_make_recon_json_with_emblem())

    decrop_report = DecropReport(source_size=(1, 1), raw_size=(1, 1), final_size=(1, 1), resized=False)

    def fake_decrop(
        raw_bytes: bytes,  # noqa: ARG001
        source: Path,  # noqa: ARG001
        raw_out: Path,
        final_out: Path,
    ) -> DecropReport:
        raw_out.parent.mkdir(parents=True, exist_ok=True)
        raw_out.write_bytes(b"fake-raw")
        final_out.write_bytes(b"fake-final")
        return decrop_report

    with (
        patch("pancratius.cover.pipeline.generate_cover", return_value=_FakeGenerationResponse()),
        patch("pancratius.cover.pipeline.vision_text", side_effect=fake_vision_text),
        patch("pancratius.cover.pipeline.decrop_to_source", side_effect=fake_decrop),
    ):
        from pancratius.cover.pipeline import translate_cover
        result = translate_cover("book-50", config, "fake-key")

    # File must be KEPT, not unlinked
    assert result.final_path is not None
    assert result.final_path.exists()
    # Status must be ok_with_caveat, not hard fail
    assert result.status == CoverStatus.OK_WITH_CAVEAT
    # ok property still works for backward-compat: ok_with_caveat is "ok enough"
    assert result.ok
    # The unresolved art_baked text must be carried in the result
    assert len(result.art_baked_leftovers) > 0
    assert any("Система дефицита" in s or "System of Scarcity" in s
               for s in result.art_baked_leftovers)


# ---------------------------------------------------------------------------
# Refinement 1b: non-art-baked leftover still hard fails (preserved behavior)
# ---------------------------------------------------------------------------


def test_non_art_baked_leftover_still_hard_fails(tmp_path: Path) -> None:
    """After cap, a leftover on a non-art-baked (overlay) element → hard fail,
    file unlinked, result.ok is False."""
    from pancratius.cover.decrop import DecropReport
    from pancratius.cover.models import CoverStatus

    config = _make_config(tmp_path)

    # Discrepancy on the title (an overlay caption) — must still be a hard fail.
    qa_fail_overlay = json.dumps({
        "verdict": "fail",
        "discrepancies": [
            {
                "kind": "cyrillic_left",
                "description": "Title «Мамона» still in Russian",
                "in_artwork": False,
            },
        ],
    })

    def fake_vision_text(
        *,
        images: list[Path],  # noqa: ARG001
        prompt: str,
        api_key: str,  # noqa: ARG001
        response_format: object = None,  # noqa: ARG001
    ) -> _FakeVisionResponse:
        if "quality assurance" in prompt:
            return _FakeVisionResponse(text=qa_fail_overlay)
        return _FakeVisionResponse(text=_make_recon_json())  # no art_baked elements

    decrop_report = DecropReport(source_size=(1, 1), raw_size=(1, 1), final_size=(1, 1), resized=False)

    def fake_decrop(
        raw_bytes: bytes,  # noqa: ARG001
        source: Path,  # noqa: ARG001
        raw_out: Path,
        final_out: Path,
    ) -> DecropReport:
        raw_out.parent.mkdir(parents=True, exist_ok=True)
        raw_out.write_bytes(b"fake-raw")
        final_out.write_bytes(b"fake-final")
        return decrop_report

    with (
        patch("pancratius.cover.pipeline.generate_cover", return_value=_FakeGenerationResponse()),
        patch("pancratius.cover.pipeline.vision_text", side_effect=fake_vision_text),
        patch("pancratius.cover.pipeline.decrop_to_source", side_effect=fake_decrop),
    ):
        from pancratius.cover.pipeline import translate_cover
        result = translate_cover("book-50", config, "fake-key")

    assert not result.ok
    assert result.status == CoverStatus.FAIL
    assert result.final_path is None
    # en.png must be unlinked
    assert not (config.output_dir / "book-50.en.png").exists()


def test_faint_art_baked_leftover_accepted_without_recon_corroboration(tmp_path: Path) -> None:
    """A cyrillic_left leftover QA marks in_artwork=True is accepted-with-caveat even
    when recon flagged NO art-baked element: recon's coarse source pass can miss a
    faint baked-in glyph (e.g. an «ОМ» corner mark) that QA sees in the output."""
    from pancratius.cover.decrop import DecropReport
    from pancratius.cover.models import CoverStatus

    config = _make_config(tmp_path)
    qa_fail_faint = json.dumps({
        "verdict": "fail",
        "discrepancies": [
            {
                "kind": "cyrillic_left",
                "description": "Cyrillic «ОМ» in the top-right artwork corner",
                "in_artwork": True,
            },
        ],
    })

    def fake_vision_text(
        *,
        images: list[Path],  # noqa: ARG001
        prompt: str,
        api_key: str,  # noqa: ARG001
        response_format: object = None,  # noqa: ARG001
    ) -> _FakeVisionResponse:
        if "quality assurance" in prompt:
            return _FakeVisionResponse(text=qa_fail_faint)
        return _FakeVisionResponse(text=_make_recon_json())  # NO art_baked element

    decrop_report = DecropReport(source_size=(1, 1), raw_size=(1, 1), final_size=(1, 1), resized=False)

    def fake_decrop(
        raw_bytes: bytes,  # noqa: ARG001
        source: Path,  # noqa: ARG001
        raw_out: Path,
        final_out: Path,
    ) -> DecropReport:
        raw_out.parent.mkdir(parents=True, exist_ok=True)
        raw_out.write_bytes(b"fake-raw")
        final_out.write_bytes(b"fake-final")
        return decrop_report

    with (
        patch("pancratius.cover.pipeline.generate_cover", return_value=_FakeGenerationResponse()),
        patch("pancratius.cover.pipeline.vision_text", side_effect=fake_vision_text),
        patch("pancratius.cover.pipeline.decrop_to_source", side_effect=fake_decrop),
    ):
        from pancratius.cover.pipeline import translate_cover
        result = translate_cover("book-50", config, "fake-key")

    assert result.status == CoverStatus.OK_WITH_CAVEAT
    assert result.final_path is not None
    assert result.final_path.exists()
    assert result.art_baked_leftovers


# ---------------------------------------------------------------------------
# Refinement 2: generation prompt names art-baked elements emphatically
# ---------------------------------------------------------------------------


def test_generation_prompt_names_art_baked_element_emphatically() -> None:
    """Art-baked elements get a more emphatic instruction naming the exact
    source Russian text and its English translation."""
    elements = [
        _element("Мамона", "Mammon", role=ElementRole.TITLE, art_baked=False),
        _element("Система дефицита", "System of Scarcity", role=ElementRole.ART_TEXT, art_baked=True),
    ]
    prompt = generation_prompt(elements, steering="")
    # The art_baked element must be identified as a seal/emblem/coin/banner
    assert any(word in prompt for word in ("seal", "emblem", "coin", "banner"))
    # The exact source Russian must be named
    assert "Система дефицита" in prompt
    # The exact English translation must be named
    assert "System of Scarcity" in prompt


# ---------------------------------------------------------------------------
# Refinement 3: refusal → retry once, then fallback backup model
# ---------------------------------------------------------------------------


def test_refusal_retries_once_then_uses_backup(tmp_path: Path) -> None:
    """A content-filter refusal retries once; if it persists, falls back to the
    backup model. The cover is NOT dropped from the batch."""
    from pancratius.cover.client import GenerationRefusal
    from pancratius.cover.decrop import DecropReport
    from pancratius.cover.models import CoverStatus

    config = _make_config(tmp_path)

    call_log: list[str] = []

    def fake_generate(src: Path, prompt: str, api_key: str, *, model: str | None = None) -> _FakeGenerationResponse:  # noqa: ARG001
        call_log.append(model or "primary")
        if model is None or model == "primary":
            raise GenerationRefusal("content filter triggered")
        # Backup model succeeds
        return _FakeGenerationResponse()

    def fake_vision_text(
        *,
        images: list[Path],  # noqa: ARG001
        prompt: str,
        api_key: str,  # noqa: ARG001
        response_format: object = None,  # noqa: ARG001
    ) -> _FakeVisionResponse:
        if "quality assurance" in prompt:
            return _FakeVisionResponse(text=_make_qa_json("pass"))
        return _FakeVisionResponse(text=_make_recon_json())

    decrop_report = DecropReport(source_size=(1, 1), raw_size=(1, 1), final_size=(1, 1), resized=False)

    def fake_decrop(
        raw_bytes: bytes,  # noqa: ARG001
        source: Path,  # noqa: ARG001
        raw_out: Path,
        final_out: Path,
    ) -> DecropReport:
        raw_out.parent.mkdir(parents=True, exist_ok=True)
        raw_out.write_bytes(b"fake-raw")
        final_out.write_bytes(b"fake-final")
        return decrop_report

    with (
        patch("pancratius.cover.pipeline.generate_cover", side_effect=fake_generate),
        patch("pancratius.cover.pipeline.vision_text", side_effect=fake_vision_text),
        patch("pancratius.cover.pipeline.decrop_to_source", side_effect=fake_decrop),
    ):
        from pancratius.cover.pipeline import translate_cover
        result = translate_cover("book-50", config, "fake-key")

    # Cover must succeed via the backup model
    assert result.ok
    assert result.status == CoverStatus.OK
    # Primary was tried (and refused), backup was used
    assert "primary" in call_log or None in call_log or call_log[0] != call_log[-1]
