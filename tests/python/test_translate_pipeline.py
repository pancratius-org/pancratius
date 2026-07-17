"""Pipeline integration without the network: a fake client drives a full draft +
revise run, proving the orchestration assembles a structure-preserving en.md with
correct frontmatter, and that selection/estimate helpers behave.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from pancratius.content_catalog import read_frontmatter, scan_catalog
from pancratius.openrouter import NoReasoning
from pancratius.translation.text.chunker import plan_chunks
from pancratius.translation.text.client import (
    ChatMessage,
    Completion,
    ModelPricing,
    TranslatorClient,
    Usage,
)
from pancratius.translation.text.config import (
    MAX_OUTPUT_TOKENS,
    TranslateConfig,
    reasoning_budget,
)
from pancratius.translation.text.document import parse_document
from pancratius.translation.text.pipeline import (
    TranslationWriteOutcome,
    _draft_chunk,
    _max_tokens_for,
    _revise_reasoning_budget,
    estimate_run,
    find_untranslated,
    translate_book,
)
from pancratius.translation.text.profile import BookProfile, ProfileError, build_profile

_RU = """\
---
kind: book
number: 1
slug: 01-test
title: Книга Света
lang: ru
description: Краткое описание.
tags:
- Свет
translation:
  source: original
---

# Заглавие

Первый абзац **света**.

<div class="lineated verse">

Строка один<BR>
Строка два

</div>
""".replace("<BR>", "  ")  # explicit two-space lineation break (editors strip trailing ws)


class _FakeClient:
    """Echoes a translation for every requested unit; profile returns a brief."""

    def fetch_pricing(self, model: str) -> ModelPricing:  # noqa: ARG002
        return ModelPricing(0.09, 0.18, 0.02)

    def complete(
        self, *, model: str, messages: Sequence[ChatMessage], **_: object
    ) -> Completion:
        last = messages[-1].content
        if "Translate ONLY the units" in last:
            ids = json.loads(last[last.index("{") :])
            payload: dict[str, object] = {
                "translations": [{"id": uid, "english": f"Light-{uid}"} for uid in ids]
            }
        elif "revising an existing draft" in last:
            payload = {"translations": []}  # no changes -> drafts stand
        else:  # profile
            payload = {
                "title_en": "Book of Light",
                "description_en": "A short description.",
                "summary": "s",
                "register": "r",
                "personas": [],
                "terms": [],
                "recurring": [],
            }
        return Completion(text=json.dumps(payload), usage=Usage(10, 10, 0, 0.001), model=model)


def _seed_book(root: Path) -> None:
    book = root / "books" / "01-test"
    book.mkdir(parents=True)
    (book / "ru.md").write_text(_RU, encoding="utf-8")


def test_translate_book_writes_structure_preserving_en(tmp_path: Path) -> None:
    content = tmp_path / "src" / "content"
    _seed_book(content)
    catalog = scan_catalog(content)
    entry = next(e for e in catalog if e.lang == "ru")

    report = translate_book(
        _FakeClient(),
        TranslateConfig(),
        entry=entry,
        catalog=catalog,
        generated_at="2026-06-17",
        dry_run=False,
        tag_labels={"Свет": "Light"},
    )

    en = entry.work_dir / "en.md"
    assert isinstance(report.outcome, TranslationWriteOutcome)
    assert report.outcome.written_path == en
    assert not report.findings  # echo translated every unit cleanly
    fm = read_frontmatter(en)
    assert fm["lang"] == "en"
    assert fm["title"] == "Book of Light"
    assert fm["tags"] == ["Light"]
    assert fm["translation"] == {
        "source": "ai",
        "model": "deepseek/deepseek-v4-flash",
        "generated_at": "2026-06-17",
    }
    body = en.read_text(encoding="utf-8").split("---\n", 2)[2]
    # Verse wrapper and the heading prefix survive; only the words changed.
    assert '<div class="lineated verse">' in body
    assert "# Light-" in body
    # The two-space verse hard break is structural -> preserved verbatim.
    assert "  \n" in body


def test_tag_with_no_glossary_entry_passes_through_raw(tmp_path: Path) -> None:
    # An unmapped RU tag is written verbatim (the tag_consistency audit flags it),
    # never silently dropped or invented.
    content = tmp_path / "src" / "content"
    _seed_book(content)
    catalog = scan_catalog(content)
    entry = next(e for e in catalog if e.lang == "ru")
    translate_book(
        _FakeClient(), TranslateConfig(), entry=entry, catalog=catalog,
        generated_at="2026-06-17", dry_run=False, tag_labels={},
    )
    fm = read_frontmatter(entry.work_dir / "en.md")
    assert fm["tags"] == ["Свет"]


def test_find_untranslated_lists_books_missing_en(tmp_path: Path) -> None:
    content = tmp_path / "src" / "content"
    _seed_book(content)
    pending = find_untranslated(scan_catalog(content))
    assert [e.number for e in pending] == [1]


class _PerAttemptClient:
    """Returns a different scripted draft reply on each successive ``complete``
    call, so a chunk's two draft attempts can disagree (transient flakiness)."""

    def __init__(self, replies: Sequence[dict[str, object]]) -> None:
        self._replies = list(replies)
        self._call = 0

    def fetch_pricing(self, model: str) -> ModelPricing:
        raise NotImplementedError

    def complete(self, *, model: str, **_: object) -> Completion:
        reply = self._replies[min(self._call, len(self._replies) - 1)]
        self._call += 1
        return Completion(text=json.dumps(reply), usage=Usage(1, 1, 0, 0.0), model=model)


def test_draft_chunk_unions_partial_replies_across_attempts() -> None:
    # Attempt 1 fills only the first unit; attempt 2 fills only the second. The
    # union must survive — a retry can never drop a unit an earlier attempt got.
    doc = parse_document("Свет.\n\nТьма.\n")
    chunk = plan_chunks(doc, TranslateConfig(chunk_source_tokens=999))[0]
    a, b = chunk.unit_ids
    client = _PerAttemptClient(
        [
            {"translations": [{"id": a, "english": "Light."}]},
            {"translations": [{"id": b, "english": "Dark."}]},
        ]
    )
    drafted = _draft_chunk(client, TranslateConfig(), brief="b", document=doc, chunk=chunk)
    assert drafted.translations == {a: "Light.", b: "Dark."}


def test_draft_chunk_keeps_good_partial_when_retry_is_unparseable() -> None:
    # Attempt 1 returns a clean unit; attempt 2 is garbage. The good unit stays.
    doc = parse_document("Свет.\n\nТьма.\n")
    chunk = plan_chunks(doc, TranslateConfig(chunk_source_tokens=999))[0]
    a, _b = chunk.unit_ids
    good = json.dumps({"translations": [{"id": a, "english": "Light."}]})

    class _GarbageSecond:
        def __init__(self) -> None:
            self._n = 0

        def fetch_pricing(self, model: str) -> ModelPricing:
            raise NotImplementedError

        def complete(self, *, model: str, **_: object) -> Completion:
            self._n += 1
            text = good if self._n == 1 else "not json at all"
            return Completion(text=text, usage=Usage(1, 1, 0, 0.0), model=model)

    drafted = _draft_chunk(_GarbageSecond(), TranslateConfig(), brief="b", document=doc, chunk=chunk)
    assert drafted.translations.get(a) == "Light."


class _RawReplyClient:
    """Returns scripted raw reply text per ``complete`` call, so an attempt can be
    malformed in ways a JSON-encoding stub could not produce."""

    def __init__(self, replies: Sequence[str]) -> None:
        self._replies = list(replies)
        self._call = 0

    def fetch_pricing(self, model: str) -> ModelPricing:
        raise NotImplementedError

    def complete(self, *, model: str, **_: object) -> Completion:
        text = self._replies[min(self._call, len(self._replies) - 1)]
        self._call += 1
        return Completion(text=text, usage=Usage(1, 1, 0, 0.0), model=model)


_BRIEF_REPLY = json.dumps(
    {
        "title_en": "The Book of Light",
        "description_en": "A short description.",
        "summary": "s",
        "register": "r",
        "personas": [],
        "terms": [],
        "recurring": [],
    }
)


def _build_brief(client: TranslatorClient, attempts: int = 3) -> BookProfile:
    return build_profile(
        client,
        TranslateConfig(profile_attempts=attempts),
        title_ru="Книга Света",
        description_ru="Краткое описание.",
        tags_ru=(),
        source_text="Свет.",
        title_precedents=(),
    ).profile


def test_profile_retries_until_the_reply_parses() -> None:
    # ds-flash returns malformed JSON for the brief the same way it does for a
    # dense draft chunk; one bad reply must not decide the book's English title.
    profile = _build_brief(_RawReplyClient(["not json at all", _BRIEF_REPLY]))
    assert profile.title_en == "The Book of Light"


def test_draft_asks_for_no_reasoning() -> None:
    # Drafting is transcription against a brief. A chain there buys nothing and spends
    # the reply's own budget: the same ds-flash prompt returns 0 reasoning tokens from
    # DeepInfra and 203 from GMICloud, so leaving it unstated is a coin flip on
    # routing. The stage says so rather than inheriting a provider default.
    doc = parse_document("Свет.\n\nТьма.\n")
    chunk = plan_chunks(doc, TranslateConfig(chunk_source_tokens=999))[0]
    a, b = chunk.unit_ids
    seen: dict[str, object] = {}

    class _Recording:
        def fetch_pricing(self, model: str) -> ModelPricing:
            raise NotImplementedError

        def complete(self, *, model: str, **kw: object) -> Completion:
            seen.update(kw)
            reply = {"translations": [{"id": a, "english": "Light."}, {"id": b, "english": "Dark."}]}
            return Completion(text=json.dumps(reply), usage=Usage(1, 1, 0, 0.0), model=model)

    _draft_chunk(_Recording(), TranslateConfig(), brief="b", document=doc, chunk=chunk)
    assert seen["reasoning"] == NoReasoning()


def test_a_stage_that_grants_a_chain_buys_the_room_for_it() -> None:
    # `max_tokens` is one pool for the chain and the reply, so a ceiling sized without
    # the cap would let a granted chain eat the units. The revise loop grants
    # `revise_reasoning_tokens`, so its ceiling must exceed the draft-shaped one.
    doc = parse_document("Свет.\n\nТьма.\n")
    config = TranslateConfig()
    chunk = plan_chunks(doc, config)[0]
    no_chain = _max_tokens_for(chunk, config)
    with_chain = _max_tokens_for(chunk, config, config.revise_reasoning_tokens)
    assert with_chain > no_chain
    content = with_chain - reasoning_budget(config.revise_reasoning_tokens, with_chain)
    assert content > config.estimate_output_tokens(chunk.source_tokens)


def test_profile_asks_for_no_reasoning_and_takes_the_house_ceiling() -> None:
    # What actually starved the brief was a 4096 ceiling too small for a big book's
    # termbase (~7k tokens for 98 terms), leaving a truncated reply that read as
    # "unparseable" and degraded to the RU title. It extracts from a book already in
    # the prompt, so it takes the house ceiling and does not deliberate first.
    seen: dict[str, object] = {}

    class _Recording(_RawReplyClient):
        def complete(self, *, model: str, **kw: object) -> Completion:
            seen.update(kw)
            return super().complete(model=model, **kw)

    _build_brief(_Recording([_BRIEF_REPLY]))
    assert seen["reasoning"] == NoReasoning()
    assert seen["max_tokens"] == MAX_OUTPUT_TOKENS


def test_profile_fails_rather_than_publishing_the_russian_title() -> None:
    # Exhausted attempts used to degrade to a brief whose title/description fell
    # back to the RU source — publishing Cyrillic on the English page, and letting
    # terms drift book-wide. Fail instead: drafted chunks are cached, so re-running
    # the book costs almost nothing.
    with pytest.raises(ProfileError):
        _build_brief(_RawReplyClient(["not json at all"]))


@pytest.mark.parametrize(
    "max_tokens",
    [400, 1086, 1654, 3000, 10000],
    ids=lambda v: f"max_tokens={v}",
)
def test_revise_reasoning_budget_never_starves_the_reply(max_tokens: int) -> None:
    # The reasoning cap shares ``max_tokens`` with the visible reply, so it must
    # leave room for content — otherwise a reasoning model returns empty text.
    budget = _revise_reasoning_budget(TranslateConfig(), max_tokens)
    assert budget < max_tokens
    assert budget <= TranslateConfig().revise_reasoning_tokens


def test_ensure_cost_fills_missing_cost_from_pricing() -> None:
    # A provider that omits `cost` must not make --max-cost fail open: the cost is
    # recomputed from live pricing and the token counts.
    from pancratius.translation.text.pipeline import _ensure_cost

    class _Priced:
        def fetch_pricing(self, model: str) -> ModelPricing:  # noqa: ARG002
            return ModelPricing(0.09, 0.18, 0.02)

        def complete(self, *, model: str, **_: object) -> Completion:
            raise NotImplementedError

    filled = _ensure_cost(Usage(1_000_000, 1_000_000, 0, None), _Priced(), TranslateConfig())
    assert filled.cost_usd == 0.09 + 0.18


def test_reconcile_seams_merges_only_rewritten_units() -> None:
    # "Свет" rendered inconsistently across three single-unit chunks (Light, Light,
    # Glow). The term scan flags the divergent seam; the fake client rewrites that
    # unit to "Light"; only it is merged back, the rest untouched.
    from pancratius.translation.text.config import TranslateConfig
    from pancratius.translation.text.pipeline import _reconcile_seams
    from pancratius.translation.text.profile import BookProfile, TermEntry

    doc = parse_document("Свет один.\n\nСвет два.\n\nСвет три.\n")
    cfg = TranslateConfig(chunk_source_tokens=2, source_chars_per_token=1.0, chunk_max_units=1)
    chunks = plan_chunks(doc, cfg)
    a, b, c = (u.id for u in doc.units)
    translations = {a: "Light one.", b: "Light two.", c: "Glow three."}
    profile = BookProfile(
        title_en="t", description_en="d", summary="", register="",
        personas=(), terms=(TermEntry(source="Свет", target="Light"),), recurring=(),
    )

    class _FixesC:
        def __init__(self) -> None:
            self.calls = 0

        def fetch_pricing(self, model: str) -> ModelPricing:  # noqa: ARG002
            return ModelPricing(0.09, 0.18, 0.02)

        def complete(self, *, model: str, **_: object) -> Completion:
            self.calls += 1
            payload = {"translations": [{"id": c, "english": "Light three."}]}
            return Completion(text=json.dumps(payload), usage=Usage(1, 1, 0, 0.0), model=model)

    client = _FixesC()
    _reconcile_seams(
        client, cfg, brief="b", document=doc, chunks=chunks,
        translations=translations, profile=profile, book_key="t",
    )
    assert client.calls >= 1  # a flagged seam was reconciled
    assert translations[c] == "Light three."  # divergent unit fixed
    assert translations[a] == "Light one."  # untouched
    assert translations[b] == "Light two."


def test_reconcile_seams_noop_when_consistent() -> None:
    # A uniformly-rendered term and no audit defects -> no seam flagged, no call.
    from pancratius.translation.text.config import TranslateConfig
    from pancratius.translation.text.pipeline import _reconcile_seams
    from pancratius.translation.text.profile import BookProfile, TermEntry

    doc = parse_document("Свет один.\n\nСвет два.\n")
    cfg = TranslateConfig(chunk_source_tokens=2, source_chars_per_token=1.0, chunk_max_units=1)
    chunks = plan_chunks(doc, cfg)
    a, b = (u.id for u in doc.units)
    translations = {a: "Light one.", b: "Light two."}
    profile = BookProfile(
        title_en="t", description_en="d", summary="", register="",
        personas=(), terms=(TermEntry(source="Свет", target="Light"),), recurring=(),
    )

    class _NeverCalled:
        def fetch_pricing(self, model: str) -> ModelPricing:
            raise NotImplementedError

        def complete(self, *, model: str, **_: object) -> Completion:  # noqa: ARG002
            raise AssertionError("no seam should be reconciled when consistent")

    _reconcile_seams(
        _NeverCalled(), cfg, brief="b", document=doc, chunks=chunks,
        translations=translations, profile=profile, book_key="t",
    )
    assert translations == {a: "Light one.", b: "Light two."}


def test_estimate_run_credits_the_cached_reference() -> None:
    doc = parse_document("Абзац один.\n\nАбзац два.\n\nАбзац три.\n")
    config = TranslateConfig(chunk_source_tokens=2, source_chars_per_token=1.0)
    chunks = plan_chunks(doc, config)
    model = config.models.draft
    cached = estimate_run(doc, config, chunks, {model: ModelPricing(0.09, 0.18, 0.02)})
    # Same job, but cache reads billed at the full input rate -> strictly dearer.
    no_cache = estimate_run(doc, config, chunks, {model: ModelPricing(0.09, 0.18, 0.09)})
    assert cached.chunks == len(chunks) >= 2
    assert 0 < cached.draft_cost_usd < no_cache.draft_cost_usd
