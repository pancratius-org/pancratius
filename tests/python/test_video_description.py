"""Unit tests for the video description splitter.

The QA gate, the deterministic fallback, and the engine's retry/fallback control
flow are all exercised offline with a stub client. No network, no API key.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import pytest

from pancratius.openrouter import (
    ChatMessage,
    Completion,
    ModelId,
    ModelPricing,
    OpenRouterError,
    Usage,
)
from pancratius.video_description import DescriptionConfig, VideoContext, draft_description
from pancratius.video_description.fallback import deterministic_split
from pancratius.video_description.models import DescriptionDraft, SplitMethod
from pancratius.video_description.qa import QaCode, verify

CONFIG = DescriptionConfig()
CTX = VideoContext(title="Заголовок видео", playlists=(), duration_seconds=160)

# A raw description the drafts below are checked against (grounding source).
SOURCE = (
    "Настоящая мысль о свете внутри. Свет живёт в сердце, а не в правилах. "
    "Ты не должен быть ярче других — достаточно быть согретым."
)


def _draft(hook: str, body: str = "", method: SplitMethod = SplitMethod.LLM) -> DescriptionDraft:
    return DescriptionDraft(hook=hook, body=body, method=method)


# ── QA ────────────────────────────────────────────────────────────────


def _codes(hook: str, body: str, *, source: str = SOURCE, ctx: VideoContext = CTX) -> set[QaCode]:
    verdict = verify(_draft(hook, body), source, ctx, CONFIG)
    return {v.code for v in verdict.violations}


def test_qa_accepts_clean_faithful_draft() -> None:
    verdict = verify(
        _draft("Свет живёт в сердце, а не в правилах.", "Настоящая мысль о свете внутри."),
        SOURCE, CTX, CONFIG,
    )
    assert verdict.ok
    assert verdict.violations == ()


def test_qa_accepts_empty_body() -> None:
    assert verify(_draft("Короткий лид о свете."), SOURCE, CTX, CONFIG).ok


@pytest.mark.parametrize(
    ("hook", "expected"),
    [
        pytest.param("Смотри тут https://t.me/x", QaCode.JUNK_IN_HOOK, id="url"),
        pytest.param("Подпишись #вера #бог", QaCode.JUNK_IN_HOOK, id="hashtag"),
        pytest.param("Пиши @pankratyus", QaCode.JUNK_IN_HOOK, id="handle"),
        pytest.param("", QaCode.EMPTY_HOOK, id="empty"),
        pytest.param("а" * 400, QaCode.HOOK_TOO_LONG, id="too-long"),
    ],
)
def test_qa_blocks_bad_hook(hook: str, expected: QaCode) -> None:
    codes = _codes(hook, "")
    assert expected in codes
    assert not verify(_draft(hook, ""), SOURCE, CTX, CONFIG).ok


def test_qa_blocks_junk_in_body() -> None:
    codes = _codes("Чистый лид.", "Мысль о свете. 💖 Поддержать проект: RUB 2200 1535 2426 2640")
    assert QaCode.JUNK_IN_BODY in codes


def test_qa_blocks_ungrounded_body() -> None:
    # Body that appears nowhere in the source = hallucination.
    codes = _codes("Чистый лид.", "Совершенно другой выдуманный текст про космос и роботов сегодня.")
    assert QaCode.BODY_UNGROUNDED in codes


def test_qa_blocks_body_drifted_to_english() -> None:
    codes = _codes("Чистый лид.", "This body drifted into English which is a translation error here.")
    assert QaCode.WRONG_LANGUAGE in codes


def test_qa_blocks_wall_of_text_body() -> None:
    long_source = "Ясная мысль о свете внутри сердца. " * 30
    wall = long_source.strip()  # >500 chars, grounded, but no blank-line paragraphs
    codes = _codes("Чистый лид.", wall, source=long_source)
    assert QaCode.BODY_NOT_PARAGRAPHED in codes
    # The same text with paragraph breaks passes.
    paragraphed = "\n\n".join([long_source.strip()[:200], long_source.strip()[200:]])
    assert QaCode.BODY_NOT_PARAGRAPHED not in _codes("Чистый лид.", paragraphed, source=long_source)


def test_qa_title_restatement_is_advisory_not_blocking() -> None:
    verdict = verify(_draft("Заголовок видео", ""), SOURCE, CTX, CONFIG)
    assert {v.code for v in verdict.violations} == {QaCode.HOOK_RESTATES_TITLE}
    assert verdict.ok  # advisory only


def test_qa_body_duplicates_hook_is_advisory() -> None:
    hook = "Свет живёт в сердце, а не в правилах."
    verdict = verify(_draft(hook, hook + " Ты не должен быть ярче других."), SOURCE, CTX, CONFIG)
    assert QaCode.BODY_DUPLICATES_HOOK in {v.code for v in verdict.violations}
    assert verdict.ok


# ── fallback ─────────────────────────────────────────────────────────


def test_fallback_strips_footer_and_splits_paragraphs() -> None:
    raw = (
        "Первая мысль лида. Она короткая.\n\n"
        "Второй абзац с развитием мысли о тишине и свете, который достаточно длинный, "
        "чтобы остаться телом, а не раствориться в коротком лиде без содержания.\n\n"
        "📢 Telegram: https://t.me/x\n💖 Поддержать проект: RUB 2200 1535 2426 2640"
    )
    draft = deterministic_split(raw, CTX, CONFIG)
    assert draft.method is SplitMethod.FALLBACK
    assert draft.hook == "Первая мысль лида. Она короткая."
    assert "Второй абзац" in draft.body
    assert "Telegram" not in draft.body and "RUB" not in draft.body
    assert "promo footer" in draft.dropped


def test_fallback_short_video_has_no_body() -> None:
    raw = "Единственная мысль дня.\n\nВторой абзац который короткому шортсу не нужен."
    short = VideoContext(title="t", duration_seconds=40)
    assert deterministic_split(raw, short, CONFIG).body == ""


def test_fallback_drops_leading_seo_line() -> None:
    raw = "Это послание от Бога о духовном кризисе и вере.\n\nРеальная мысль начинается здесь."
    draft = deterministic_split(raw, CTX, CONFIG)
    assert "SEO keyword line" in draft.dropped
    assert draft.hook.startswith("Реальная мысль")


def test_fallback_output_is_never_junky() -> None:
    from pancratius.video_description.patterns import junk_categories

    raw = "Мысль. #тег @handle https://x.ru\n\n💖 RUB 2200 1535 2426 2640"
    draft = deterministic_split(raw, CTX, CONFIG)
    assert junk_categories(draft.hook) == []
    assert junk_categories(draft.body) == []


@pytest.mark.parametrize(
    "junk",
    [
        pytest.param("2200153524262640", id="card-16-continuous"),
        pytest.param("2200  1535  2426  2640", id="card-double-spaced"),
        pytest.param("2200.1535.2426.2640", id="card-dotted"),
        pytest.param("напиши:@pankratyus", id="handle-glued"),
        pytest.param("(@pankratyus)", id="handle-parens"),
        pytest.param("telegram.me/x", id="bare-domain-me"),
        pytest.param("rutube.ru/x", id="bare-domain-ru"),
        pytest.param("<img onerror=hack>", id="html-tag"),
    ],
)
def test_junk_patterns_resist_evasion(junk: str) -> None:
    from pancratius.video_description.patterns import junk_categories

    assert junk_categories(junk) != []


def test_junk_patterns_do_not_flag_clean_prose() -> None:
    from pancratius.video_description.patterns import junk_categories

    clean = "Свет живёт в сердце, а не в правилах. Если a < b, это просто сравнение. Крест ✝ — знак."
    assert junk_categories(clean) == []


# ── engine control flow ──────────────────────────────────────────────


@dataclass
class _StubClient:
    replies: list[str]
    truncated: bool = False
    error: bool = False
    calls: int = 0

    def complete(
        self,
        *,
        model: ModelId,
        messages: Sequence[ChatMessage],
        temperature: float,
        max_tokens: int,
        response_format: dict[str, Any] | None = None,
        reasoning_max_tokens: int | None = None,
    ) -> Completion:
        del messages, temperature, max_tokens, response_format, reasoning_max_tokens
        self.calls += 1
        if self.error:
            raise OpenRouterError("stub failure")
        reply = self.replies[min(self.calls - 1, len(self.replies) - 1)]
        return Completion(text=reply, usage=Usage(10, 10, 0, 0.001), model=model, truncated=self.truncated)

    def fetch_pricing(self, model: ModelId) -> ModelPricing:
        del model
        return ModelPricing(0.1, 0.4, None)


def _reply(hook: str, body: str) -> str:
    return json.dumps({"hook": hook, "body_markdown": body, "dropped": []}, ensure_ascii=False)


def test_engine_happy_path_returns_llm_draft() -> None:
    client = _StubClient([_reply("Свет живёт в сердце.", "Настоящая мысль о свете внутри.")])
    draft, usage = draft_description(SOURCE, CTX, client=client, config=CONFIG)
    assert draft.method is SplitMethod.LLM
    assert draft.hook == "Свет живёт в сердце."
    assert client.calls == 1
    assert usage.completion_tokens == 10


def test_engine_retries_then_accepts() -> None:
    bad = _reply("Смотри https://t.me/x", "Настоящая мысль о свете внутри.")  # junk hook
    good = _reply("Свет живёт в сердце.", "Настоящая мысль о свете внутри.")
    client = _StubClient([bad, good])
    draft, _ = draft_description(SOURCE, CTX, client=client, config=CONFIG)
    assert draft.method is SplitMethod.LLM
    assert client.calls == 2


def test_engine_falls_back_after_exhausting_attempts() -> None:
    bad = _reply("Смотри https://t.me/x", "Совсем другой выдуманный текст про роботов и космос.")
    client = _StubClient([bad])  # junk hook + ungrounded body, every attempt
    draft, _ = draft_description(SOURCE, CTX, client=client, config=CONFIG)
    assert draft.method is SplitMethod.FALLBACK
    assert client.calls == CONFIG.attempts


def test_engine_falls_back_on_ungrounded_body_alone() -> None:
    # Hook is clean and grounded; only the invented body drives the rejection.
    bad = _reply("Свет живёт в сердце.", "Пожертвуй сегодня и получи исцеление и богатство завтра.")
    client = _StubClient([bad])
    draft, _ = draft_description(SOURCE, CTX, client=client, config=CONFIG)
    assert draft.method is SplitMethod.FALLBACK


def test_engine_treats_truncation_as_failure() -> None:
    client = _StubClient([_reply("ok", "Настоящая мысль о свете внутри.")], truncated=True)
    draft, _ = draft_description(SOURCE, CTX, client=client, config=CONFIG)
    assert draft.method is SplitMethod.FALLBACK
    assert client.calls == CONFIG.attempts


def test_engine_api_error_falls_back_immediately() -> None:
    client = _StubClient([], error=True)
    draft, _ = draft_description(SOURCE, CTX, client=client, config=CONFIG)
    assert draft.method is SplitMethod.FALLBACK
    assert client.calls == 1


def test_engine_unparseable_reply_retries() -> None:
    client = _StubClient(["not json at all", _reply("Свет.", "Настоящая мысль о свете внутри.")])
    draft, _ = draft_description(SOURCE, CTX, client=client, config=CONFIG)
    assert draft.method is SplitMethod.LLM
    assert client.calls == 2


def test_engine_no_client_uses_fallback() -> None:
    draft, usage = draft_description(SOURCE, CTX, client=None, config=CONFIG)
    assert draft.method is SplitMethod.FALLBACK
    assert usage == Usage.empty()


def test_engine_drops_redundant_short_body() -> None:
    thought = "Ты не жертва времени. Ты место, где Бог решил узнать Себя."
    client = _StubClient([_reply(thought, thought)])  # body merely restates the hook
    short = VideoContext(title="Апокалипсис за 40 секунд", duration_seconds=40)
    draft, _ = draft_description(thought, short, client=client, config=CONFIG)
    assert draft.method is SplitMethod.LLM
    assert draft.body == ""  # tidied away — the lede already carries the thought


def test_engine_keeps_richer_short_body() -> None:
    hook = "Царство внутри тебя."
    body = "Царство внутри тебя, а не снаружи.\n\nЭто не религия, а пробуждение сердца прямо сейчас."
    client = _StubClient([_reply(hook, body)])
    short = VideoContext(title="О Царстве", duration_seconds=40)
    draft, _ = draft_description(body, short, client=client, config=CONFIG)
    assert draft.body != ""  # richer than the hook — kept


# ── hardened safety guards (from adversarial review) ─────────────────


def test_qa_blocks_html_in_body() -> None:
    codes = _codes("Чистый лид.", f"{SOURCE}\n\n<script>alert(1)</script>")
    assert QaCode.JUNK_IN_BODY in codes


def test_qa_blocks_ukrainian_drift() -> None:
    # Ukrainian-specific letters (і/ї/є) are a known corpus defect, not Russian.
    assert QaCode.WRONG_LANGUAGE in _codes("Чистий лід про світло душі твоєї.", "")


def test_normalize_locale_text_applies_terminology_and_quotes() -> None:
    from pancratius.localization import TermReplacement, normalize_locale_text

    terms = (
        TermReplacement("Holy Russia", "Holy Rus", insensitive=True),
        TermReplacement("Pankratius", "Pancratius", insensitive=False),
    )
    out = normalize_locale_text('In Holy Russia, Pankratius said "come home." He meant it.', "en", terms)
    assert "Holy Rus" in out and "Holy Russia" not in out
    assert "Pancratius" in out and "Pankratius" not in out
    assert "“come home.”" in out  # opening + closing curly, facing right


def test_normalize_locale_text_terminology_respects_case_flag() -> None:
    from pancratius.localization import TermReplacement, normalize_locale_text

    # "Holy Russia" is match:insensitive in the glossary → lowercase must be fixed;
    # "Pankratius" is case-sensitive → a lowercase slug token must be left alone.
    terms = (
        TermReplacement("Holy Russia", "Holy Rus", insensitive=True),
        TermReplacement("Pankratius", "Pancratius", insensitive=False),
    )
    assert "Holy Rus" in normalize_locale_text("in holy russia we live", "en", terms)
    assert "sergey-pankratius" in normalize_locale_text("see sergey-pankratius online", "en", terms)


def test_normalize_locale_text_closes_multi_paragraph_quote() -> None:
    from pancratius.localization import normalize_locale_text

    # A quote opening in one paragraph and closing at the very end: the closing
    # mark must curl closed, not backwards.
    body = 'He said:\n\n"This is the first line.\n\nAnd the last, in all its glory."'
    out = normalize_locale_text(body, "en")
    assert out.endswith('glory.”')
    assert '"' not in out


def test_normalize_locale_text_handles_single_quotes_and_apostrophes() -> None:
    from pancratius.localization import normalize_locale_text

    assert normalize_locale_text("'Don't,' he said.", "en") == "‘Don’t,’ he said."


def test_qa_accepts_english_for_en_locale() -> None:
    src = "The light lives in the heart, not in rules. You do not have to be brighter than others."
    en_ctx = VideoContext(title="On the light", lang="en", duration_seconds=160)
    verdict = verify(
        _draft("The light lives in the heart, not in rules.", "You do not have to be brighter than others."),
        src, en_ctx, CONFIG,
    )
    assert verdict.ok


def test_qa_rejects_wrong_language_for_locale() -> None:
    en_ctx = VideoContext(title="On the light", lang="en", duration_seconds=160)
    ru_body_in_en = verify(
        _draft("The light lives in the heart.", "Свет живёт в сердце, а не в правилах, друг навсегда."),
        "The light lives in the heart.", en_ctx, CONFIG,
    )
    assert QaCode.WRONG_LANGUAGE in {v.code for v in ru_body_in_en.violations}
    # English in a Russian-locale hook is likewise a drift (default CTX is ru).
    assert QaCode.WRONG_LANGUAGE in _codes("This entire hook is written in English words here", "")


def test_qa_blocks_ungrounded_hook() -> None:
    # A hook whose vocabulary is invented, not drawn from the description.
    codes = _codes("Купите криптовалюту сегодня получите гарантированную прибыль завтра быстро", "")
    assert QaCode.HOOK_UNGROUNDED in codes


def test_qa_faithful_paraphrased_hook_passes_grounding() -> None:
    # A distilled hook that reuses the source vocabulary clears the loose floor.
    verdict = verify(_draft("Свет живёт в сердце, а не в правилах — ты согрет.", ""), SOURCE, CTX, CONFIG)
    assert QaCode.HOOK_UNGROUNDED not in {v.code for v in verdict.violations}


def test_qa_blocks_diluted_fabrication() -> None:
    # A single invented sentence bolted onto a faithful body must not hide behind
    # the aggregate — per-sentence grounding catches it.
    body = SOURCE + "\n\nПожертвуй сегодня, и Бог исцелит твою болезнь навсегда."
    assert QaCode.BODY_UNGROUNDED in _codes("Свет живёт в сердце.", body)


def test_qa_blocks_short_fabricated_body() -> None:
    assert QaCode.BODY_UNGROUNDED in _codes("Чистый лид.", "Покайся, грешник, конец близок.")


def test_qa_grounds_verbatim_multi_sentence_body() -> None:
    # Every sentence is a verbatim span of the source → fully grounded.
    verdict = verify(
        _draft("Лид.", "Свет живёт в сердце, а не в правилах. Ты не должен быть ярче других."),
        SOURCE, CTX, CONFIG,
    )
    assert QaCode.BODY_UNGROUNDED not in {v.code for v in verdict.violations}


def test_fallback_falls_back_to_title_when_all_junk() -> None:
    # The description is nothing but a donation block + link — the fallback must
    # never emit that as the hook; it uses the (clean) title instead.
    raw = "💖 Поддержать проект: RUB 2200 1535 2426 2640\nhttps://t.me/SPankratyus"
    ctx = VideoContext(title="Слово о свете", duration_seconds=40)
    draft = deterministic_split(raw, ctx, CONFIG)
    from pancratius.video_description.patterns import junk_categories

    assert draft.hook == "Слово о свете"
    assert junk_categories(draft.hook) == []
    assert draft.body == ""


def test_fallback_footer_detection_is_trailing_only() -> None:
    # A mid-message line that merely OPENS with a footer word ("Дзен") must not
    # truncate the real text that follows it.
    raw = (
        "Первая мысль о тишине и её глубине для сердца человека.\n\n"
        "Дзен — это только слово, но за ним живёт настоящий покой, который не исчезает.\n\n"
        "И этот покой ближе, чем ты думаешь, он уже внутри тебя прямо сейчас."
    )
    draft = deterministic_split(raw, CTX, CONFIG)
    assert "покой" in draft.body  # the post-"Дзен" text survived
