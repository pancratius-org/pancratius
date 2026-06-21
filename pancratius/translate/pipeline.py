"""Orchestration: turn one ``ru.md`` into an ``en.md`` translation.

Per book: read the source, build the brief (profile pre-pass), plan chunks, draft
each chunk with the whole book as cached reference, run the deterministic checks,
run the source-aware revise pass, then re-assemble the translated units into the
source's exact structure and write ``en.md`` beside ``ru.md`` (recording
``translation.source: ai``). ``--dry-run`` produces the plan and a live-priced
cost estimate without calling the generative endpoint or needing an API key.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from pancratius.content_catalog import CatalogEntry, dump_frontmatter, split_frontmatter
from pancratius.kinds import CorpusWorkKind
from pancratius.locales import Locale
from pancratius.translate.cache import BriefCacheEntry, CacheEntry, TranslationCache
from pancratius.translate.checks import Finding, Severity, check_translation
from pancratius.translate.chunker import Chunk, plan_chunks
from pancratius.translate.client import ModelPricing, TranslatorClient, Usage
from pancratius.translate.config import ModelId, TranslateConfig
from pancratius.translate.diagnostics import (
    Seam,
    audit_book,
    build_digest,
    inconsistent_term_seams,
    seam_windows,
)
from pancratius.translate.document import (
    Document,
    TextUnit,
    Translations,
    UnitId,
    parse_document,
)
from pancratius.translate.profile import (
    BookProfile,
    TagLabels,
    build_profile,
    effective_terms,
    str_tuple,
    title_precedents,
)
from pancratius.translate.prompts import (
    TermEntry,
    TitlePrecedent,
    build_brief,
    reconcile_messages,
    revise_messages,
    translate_messages,
)
from pancratius.translate.schema import parse_translations, translation_format

# Markdown YAML frontmatter as `content_catalog` reads/writes it: a mapping of
# string keys to JSON-ish values. Named so the en.md assembly reads as frontmatter,
# not an anonymous blob.
type Frontmatter = dict[str, Any]

logger = logging.getLogger(__name__)

_MAX_OUTPUT_TOKENS = 16000
_REFERENCE_OVERHEAD = 1.02  # plain-text reference: just newlines around the source


class TranslateError(RuntimeError):
    """A translation run could not proceed (bad input, refused write, oversize book)."""


@dataclass(frozen=True, slots=True)
class CostEstimate:
    source_tokens: int
    output_tokens: int
    reference_tokens: int
    chunks: int
    draft_cost_usd: float
    revise_cost_usd: float
    profile_cost_usd: float

    @property
    def total_usd(self) -> float:
        return self.draft_cost_usd + self.revise_cost_usd + self.profile_cost_usd


@dataclass(frozen=True, slots=True)
class TranslationReport:
    book_key: str
    units: int
    chunks: int
    dry_run: bool
    usage: Usage = Usage.empty()
    findings: tuple[Finding, ...] = ()
    estimate: CostEstimate | None = None
    written_path: Path | None = None
    profile: BookProfile | None = None
    blocking: tuple[Finding, ...] = field(default_factory=tuple)
    cached_chunks: int = 0
    api_chunks: int = 0
    # End-of-run diagnostics lines (cross-seam + frontmatter/script warnings),
    # printed under the existing summary. Empty for a clean book.
    digest: tuple[str, ...] = ()


def find_untranslated(catalog: Sequence[CatalogEntry], *, kind: CorpusWorkKind = "book") -> list[CatalogEntry]:
    """The source entries of works that have a ``ru`` but no ``<target>`` language —
    i.e. the books still missing a translation."""
    langs_by_number: dict[int, set[Locale]] = {}
    source_by_number: dict[int, CatalogEntry] = {}
    for entry in catalog:
        if entry.kind != kind:
            continue
        langs_by_number.setdefault(entry.number, set()).add(entry.lang)
        if entry.lang == "ru":
            source_by_number[entry.number] = entry
    pending = [
        source_by_number[number]
        for number, langs in langs_by_number.items()
        if "ru" in langs and "en" not in langs and number in source_by_number
    ]
    return sorted(pending, key=lambda entry: entry.number)


def _brief_for(
    profile: BookProfile,
    *,
    title_ru: str,
    terms: Sequence[TermEntry],
    precedents: Sequence[TitlePrecedent],
) -> str:
    return build_brief(
        title_ru=title_ru,
        title_en=profile.title_en,
        summary=profile.summary,
        register=profile.register,
        personas=profile.persona_lines(),
        terms=terms,
        title_precedents=precedents,
    )


def _chunk_units(document: Document, chunk: Chunk) -> list[TextUnit]:
    index = document.unit_index()
    return [index[uid] for uid in chunk.unit_ids]


def _max_tokens_for(chunk: Chunk, config: TranslateConfig) -> int:
    # max_tokens is only a ceiling — you pay for tokens actually generated — so be
    # generous. Undersizing truncates the JSON reply and silently drops units; over-
    # sizing costs nothing. Budget covers the translations plus per-unit JSON framing.
    translation = config.estimate_output_tokens(chunk.source_tokens)
    return min(round(translation * 2) + len(chunk.unit_ids) * 30 + 1024, _MAX_OUTPUT_TOKENS)


def _revise_reasoning_budget(config: TranslateConfig, max_tokens: int) -> int:
    """Cap the revise critic's hidden reasoning, but never let that cap eat the
    whole completion: ``max_tokens`` covers reasoning AND visible content, so on a
    small chunk a fixed 3k cap would starve the reply to empty (the ds-flash
    runaway). Reserve at least half the budget for the actual revised units."""
    return min(config.revise_reasoning_tokens, max_tokens // 2)


@dataclass(frozen=True, slots=True)
class DraftedChunk:
    """One chunk's draft outcome: the translated units accumulated across attempts
    and the total usage spent producing them."""

    translations: dict[UnitId, str]
    usage: Usage


def _untranslated(text: str, source: str) -> bool:
    """A draft unit is untranslated when it is blank OR echoes the Cyrillic source —
    the model sometimes returns the Russian verbatim instead of translating it, which
    is non-blank so the bare completeness check misses it. A faithful English unit is
    Latin-dominant; a Cyrillic-dominant draft of a Cyrillic source was not done."""
    stripped = text.strip()
    if not stripped:
        return True
    cyr = sum(1 for c in stripped if "Ѐ" <= c <= "ӿ")
    lat = sum(1 for c in stripped if ("a" <= c <= "z") or ("A" <= c <= "Z"))
    src_cyr = sum(1 for c in source if "Ѐ" <= c <= "ӿ")
    return cyr > lat and src_cyr >= 4


def _draft_chunk(
    client: TranslatorClient,
    config: TranslateConfig,
    *,
    brief: str,
    document: Document,
    chunk: Chunk,
) -> DraftedChunk:
    """Draft one chunk, retrying if the reply is unparseable or misses units
    (transient model flakiness). Each attempt only ADDS units the prior ones left
    blank, so a retry can never wipe out good text a worse later reply omitted; a
    still-incomplete chunk surfaces as a critical check, never a silent partial.

    If the primary model leaves units blank after all attempts, a final pass retries
    them on the backup model with no full-source reference: deepseek-v4-flash
    intermittently returns null content under the strict per-unit JSON schema on some
    dense passages, and a different model (with a smaller input) clears them. The
    revise pass then re-homogenises the voice."""
    units = _chunk_units(document, chunk)
    source_by_id = {u.id: u.source for u in units}
    usage = Usage.empty()
    result: dict[UnitId, str] = {}
    last_reply = ""

    def run_attempts(reference_units: Sequence[TextUnit], n: int, model: ModelId) -> bool:
        """Draft up to ``n`` times on ``model`` against ``reference_units``; fill only
        units still untranslated (blank or echoing the source). True once all are done."""
        nonlocal usage, last_reply
        for attempt in range(n):
            completion = client.complete(
                model=model,
                messages=translate_messages(
                    brief=brief, full_source_units=reference_units, chunk_units=units
                ),
                temperature=config.draft_temperature,
                max_tokens=_max_tokens_for(chunk, config),
                response_format=translation_format(chunk.unit_ids),
            )
            usage += completion.usage
            last_reply = completion.text or ""
            try:
                parsed = _only_requested(parse_translations(completion.text), chunk.unit_ids)
            except (json.JSONDecodeError, ValueError):
                parsed = {}
            for uid in chunk.unit_ids:
                cand, src = parsed.get(uid, ""), source_by_id[uid]
                if _untranslated(result.get(uid, ""), src) and cand.strip() and not _untranslated(cand, src):
                    result[uid] = cand
            if all(not _untranslated(result.get(uid, ""), source_by_id[uid]) for uid in chunk.unit_ids):
                return True
            logger.warning(
                "draft chunk %d incomplete (attempt %d/%d, %s); retrying",
                chunk.index + 1, attempt + 1, n, model,
            )
        return False

    if run_attempts(document.units, config.draft_attempts, config.models.draft):
        return DraftedChunk(translations=result, usage=usage)
    # Primary model fell short. Retry the blanks on the backup model with no
    # full-source reference (smaller input, different model → clears the stall).
    backup = config.models.backup_draft
    if backup and backup != config.models.draft:
        logger.warning(
            "draft chunk %d incomplete on %s; retrying blanks on backup %s",
            chunk.index + 1, config.models.draft, backup,
        )
        if run_attempts((), max(2, config.draft_attempts // 2), backup):
            return DraftedChunk(translations=result, usage=usage)
    unresolved = [u for u in chunk.unit_ids if _untranslated(result.get(u, ""), source_by_id[u])]
    if unresolved:
        logger.warning(
            "draft chunk %d UNRESOLVED: %d/%d units blank or untranslated; last reply len=%d, reply[:500]=%r",
            chunk.index + 1, len(unresolved), len(chunk.unit_ids), len(last_reply), last_reply[:500],
        )
    return DraftedChunk(translations=result, usage=usage)


def estimate_run(
    document: Document,
    config: TranslateConfig,
    chunks: Sequence[Chunk],
    pricing: Mapping[ModelId, ModelPricing],
) -> CostEstimate:
    """A transparent, corpus-calibrated cost estimate. Real billing comes back in
    each call's ``usage``; this sizes the job before spending."""
    source_tokens = sum(config.estimate_source_tokens(len(u.source)) for u in document.units)
    output_tokens = config.estimate_output_tokens(source_tokens)
    reference_tokens = round(source_tokens * _REFERENCE_OVERHEAD)
    n = max(len(chunks), 1)

    draft = pricing[config.models.draft]
    # First chunk pays the reference fresh; later chunks read it from cache. Every
    # chunk also sends its own units fresh (≈ the whole source once across chunks).
    draft_fresh = reference_tokens + source_tokens
    draft_cached = reference_tokens * (n - 1)
    draft_cost = draft.cost(draft_fresh + draft_cached, output_tokens, draft_cached)

    revise_cost = 0.0
    if config.revise:
        rev = pricing[config.models.revise]
        # Each chunk re-sends source+draft for its units (≈ 2× source overall);
        # only changed units are regenerated (~half the draft output, generously).
        revise_fresh = source_tokens * 2
        revise_out = round(output_tokens * 0.5)
        revise_cost = rev.cost(revise_fresh, revise_out, 0)

    profile_cost = 0.0
    if config.build_profile:
        prof = pricing[config.models.profile]
        profile_cost = prof.cost(source_tokens, 2048, 0)

    return CostEstimate(
        source_tokens=source_tokens,
        output_tokens=output_tokens,
        reference_tokens=reference_tokens,
        chunks=n,
        draft_cost_usd=draft_cost,
        revise_cost_usd=revise_cost,
        profile_cost_usd=profile_cost,
    )


def _distinct_pricing(client: TranslatorClient, config: TranslateConfig) -> dict[ModelId, ModelPricing]:
    models = {config.models.draft, config.models.revise, config.models.profile}
    return {model: client.fetch_pricing(model) for model in models}


def _load_source(entry: CatalogEntry) -> tuple[Frontmatter, Document]:
    fm, body = split_frontmatter(entry.md_path.read_text(encoding="utf-8"))
    return fm, parse_document(body)


def _en_frontmatter(
    ru_fm: Frontmatter,
    *,
    profile: BookProfile,
    work_dir: Path,
    model: ModelId,
    generated_at: str,
    tag_labels: TagLabels,
) -> Frontmatter:
    fm: Frontmatter = dict(ru_fm)
    fm["lang"] = "en"
    fm["title"] = profile.title_en
    fm["description"] = profile.description_en
    # Tags are the RU entry's tags mapped through the glossary — one concept, one
    # canonical EN label across the corpus. An unmapped tag passes through for the
    # tag_consistency audit to flag.
    ru_tags = str_tuple(ru_fm.get("tags"))
    fm["tags"] = [tag_labels.get(t, t) for t in ru_tags]
    cover = next((p for p in sorted(work_dir.glob("cover.en.*"))), None)
    if cover is not None:
        fm["cover"] = f"./{cover.name}"
    fm["translation"] = {"source": "ai", "model": model, "generated_at": generated_at}
    return fm


def translate_book(
    client: TranslatorClient | None,
    config: TranslateConfig,
    *,
    entry: CatalogEntry,
    catalog: Sequence[CatalogEntry],
    glossary: Sequence[TermEntry] = (),
    generated_at: str,
    dry_run: bool,
    replace: bool = False,
    cache_dir: Path | None = None,
    tag_labels: TagLabels | None = None,
) -> TranslationReport:
    ru_fm, document = _load_source(entry)
    chunks = plan_chunks(document, config)
    precedents = title_precedents(catalog)
    en_path = entry.work_dir / "en.md"

    if dry_run:
        if client is None:
            raise TranslateError("a client is required to fetch live pricing for the estimate")
        pricing = _distinct_pricing(client, config)
        estimate = estimate_run(document, config, chunks, pricing)
        return TranslationReport(
            book_key=entry.work_key,
            units=len(document.units),
            chunks=len(chunks),
            dry_run=True,
            estimate=estimate,
        )

    if client is None:
        raise TranslateError("a client is required for a real translation run")
    if en_path.exists() and not replace:
        raise TranslateError(f"{en_path} exists; pass --replace to overwrite an existing translation")
    source_tokens_total = sum(config.estimate_source_tokens(len(u.source)) for u in document.units)
    if source_tokens_total > config.reference_token_budget:
        raise TranslateError(
            f"{entry.work_key}: ~{source_tokens_total} source tokens exceeds the "
            f"reference budget {config.reference_token_budget}; lower --chunk-tokens "
            "is not enough — this book needs windowed reference (not yet implemented)."
        )

    cache = TranslationCache(cache_dir) if cache_dir is not None else None
    tags_ru = str_tuple(ru_fm.get("tags"))

    usage = Usage.empty()
    profile: BookProfile
    brief: str

    if cache is not None:
        bk = cache.brief_key(
            config.models.profile,
            document.source_text(),
            title_ru=entry.title,
            description_ru=entry.description,
            tags_ru=tags_ru,
        )
        cached_brief = cache.get_brief(bk)
        if cached_brief is not None:
            brief = cached_brief.brief
            profile = _profile_from_json_str(
                cached_brief.profile_json,
                fallback_title=entry.title,
                fallback_desc=entry.description,
            )
            logger.info("profile brief from cache")
        else:
            profile_result = build_profile(
                client,
                config,
                title_ru=entry.title,
                description_ru=entry.description,
                tags_ru=tags_ru,
                source_text=document.source_text(),
                title_precedents=precedents,
            )
            usage += profile_result.usage
            profile = profile_result.profile
            terms = effective_terms(profile, glossary)
            brief = _brief_for(profile, title_ru=entry.title, terms=terms, precedents=precedents)
            cache.put_brief(bk, BriefCacheEntry(brief=brief, profile_json=_profile_to_json(profile)))
    else:
        profile_result = build_profile(
            client,
            config,
            title_ru=entry.title,
            description_ru=entry.description,
            tags_ru=tags_ru,
            source_text=document.source_text(),
            title_precedents=precedents,
        )
        usage += profile_result.usage
        profile = profile_result.profile
        terms = effective_terms(profile, glossary)
        brief = _brief_for(profile, title_ru=entry.title, terms=terms, precedents=precedents)

    translations: dict[UnitId, str] = {}
    cached_chunks = 0
    api_chunks = 0
    # Track which chunk indices were served from cache to skip them in the revise loop.
    cache_hit_indices: set[int] = set()

    for chunk in chunks:
        if cache is not None:
            ck = cache.chunk_key(
                config.models.draft,
                brief,
                tuple(u.source for u in _chunk_units(document, chunk)),
            )
            hit = cache.get_chunk(ck)
            if hit is not None:
                translations.update(hit.unit_translations)
                cached_chunks += 1
                cache_hit_indices.add(chunk.index)
                logger.info("chunk %d/%d from cache", chunk.index + 1, len(chunks))
                continue

        drafted = _draft_chunk(client, config, brief=brief, document=document, chunk=chunk)
        usage += drafted.usage
        translations.update(drafted.translations)
        api_chunks += 1
        logger.info("drafted chunk %d/%d", chunk.index + 1, len(chunks))

        # When not revising, write to cache immediately after a fully successful draft.
        if not config.revise and cache is not None:
            chunk_result = {uid: translations.get(uid, "") for uid in chunk.unit_ids}
            if all(v.strip() for v in chunk_result.values()):
                ck = cache.chunk_key(
                    config.models.draft,
                    brief,
                    tuple(u.source for u in _chunk_units(document, chunk)),
                )
                cache.put_chunk(ck, CacheEntry(unit_translations=chunk_result))

    if config.revise:
        for chunk in chunks:
            if chunk.index in cache_hit_indices:
                continue  # already fully translated from cache

            units = _chunk_units(document, chunk)
            draft_subset = {uid: translations.get(uid, "") for uid in chunk.unit_ids}
            max_tokens = _max_tokens_for(chunk, config)
            completion = client.complete(
                model=config.models.revise,
                messages=revise_messages(brief=brief, units=units, draft=draft_subset),
                temperature=config.revise_temperature,
                max_tokens=max_tokens,
                response_format=translation_format(chunk.unit_ids),
                reasoning_max_tokens=_revise_reasoning_budget(config, max_tokens),
            )
            usage += completion.usage
            # Revise is best-effort: an empty or unparseable reply keeps the draft
            # for this chunk rather than failing the book.
            try:
                improved = _only_requested(parse_translations(completion.text), chunk.unit_ids)
            except (json.JSONDecodeError, ValueError):
                logger.warning("revise chunk %d/%d unparseable; keeping draft", chunk.index + 1, len(chunks))
            else:
                translations.update({uid: text for uid, text in improved.items() if text.strip()})
                logger.info("revised chunk %d/%d", chunk.index + 1, len(chunks))

            # Write cache after revise only when every unit in the chunk is non-blank.
            if cache is not None:
                chunk_result = {uid: translations.get(uid, "") for uid in chunk.unit_ids}
                if all(v.strip() for v in chunk_result.values()):
                    ck = cache.chunk_key(
                        config.models.draft,
                        brief,
                        tuple(u.source for u in units),
                    )
                    cache.put_chunk(ck, CacheEntry(unit_translations=chunk_result))

    if config.reconcile:
        usage += _reconcile_seams(
            client, config, brief=brief, document=document, chunks=chunks,
            translations=translations, profile=profile, book_key=entry.work_key,
        )

    # en_fm before the final check so check_translation can scan it for leftover
    # Cyrillic; _en_frontmatter is pure (no I/O), so building it early is free.
    en_fm = _en_frontmatter(
        ru_fm,
        profile=profile,
        work_dir=entry.work_dir,
        model=config.models.draft,
        generated_at=generated_at,
        tag_labels=tag_labels or {},
    )
    findings = tuple(check_translation(document, translations, en_fm=en_fm))
    blocking = tuple(f for f in findings if f.severity >= Severity.CRITICAL)
    if blocking:
        raise TranslateError(
            f"{entry.work_key}: {len(blocking)} unit(s) were not translated; refusing to write "
            "a partial en.md. Re-run, or inspect with --dry-run."
        )

    audit = audit_book(document, parse_document(document.render(translations)), config, book_key=entry.work_key)
    digest = build_digest(audit, [f for f in findings if f.code in _DIGEST_WARNING_CODES])

    body = document.render(translations)
    en_path.write_text(dump_frontmatter(en_fm) + body, encoding="utf-8")
    return TranslationReport(
        book_key=entry.work_key,
        units=len(document.units),
        chunks=len(chunks),
        dry_run=False,
        usage=_ensure_cost(usage, client, config),
        findings=findings,
        written_path=en_path,
        profile=profile,
        cached_chunks=cached_chunks,
        api_chunks=api_chunks,
        digest=digest,
    )


# Warning codes whose findings feed the end-of-run digest (the actionable,
# review-worthy ones); the rest stay in `findings` for the count summary.
_DIGEST_WARNING_CODES = frozenset({"frontmatter_cyrillic", "mixed_script", "byte_equal"})


def _flagged_seams(
    document: Document,
    config: TranslateConfig,
    chunks: Sequence[Chunk],
    translations: Translations,
    profile: BookProfile,
    *,
    book_key: str,
) -> list[Seam]:
    """Seams worth a reconcile call: those with an at_seam audit finding, plus those
    whose window straddles a brief term rendered two ways across the book. A book has
    hundreds of seams; reconciling all is wasteful, so this keeps it to the suspect few."""
    seams = seam_windows(document, chunks)
    if not seams:
        return []
    target_doc = parse_document(document.render(translations))
    audit = audit_book(document, target_doc, config, book_key=book_key)
    # Map each at_seam finding's source-unit index to the seam window it sits in.
    by_unit = {u.id: i for i, seam in enumerate(seams) for u in seam.window}
    by_index = {i: u.id for i, u in enumerate(document.units)}
    flagged: set[int] = set()
    for f in audit.at_seam():
        uid = by_index.get(f.index)
        if uid is not None and uid in by_unit:
            flagged.add(by_unit[uid])
    terms = [(t.source, t.target) for t in profile.terms]
    flagged |= inconsistent_term_seams(document, translations, seams, terms=terms)
    return [seams[i] for i in sorted(flagged)]


def _reconcile_seams(
    client: TranslatorClient,
    config: TranslateConfig,
    *,
    brief: str,
    document: Document,
    chunks: Sequence[Chunk],
    translations: dict[UnitId, str],
    profile: BookProfile,
    book_key: str,
) -> Usage:
    """Reconcile only flagged chunk boundaries: for each, send the straddling window
    (both sides' source + current English) and merge back ONLY the units the model
    rewrote. Best-effort — an empty/unparseable reply leaves the seam untouched."""
    usage = Usage.empty()
    seams = _flagged_seams(document, config, chunks, translations, profile, book_key=book_key)
    for seam in seams:
        window_ids = tuple(u.id for u in seam.window)
        draft_subset = {uid: translations.get(uid, "") for uid in window_ids}
        completion = client.complete(
            model=config.models.revise,
            messages=reconcile_messages(brief=brief, units=seam.window, draft=draft_subset),
            temperature=config.revise_temperature,
            max_tokens=_max_tokens_for_units(seam.window, config),
            response_format=translation_format(window_ids),
        )
        usage += completion.usage
        try:
            improved = _only_requested(parse_translations(completion.text), window_ids)
        except (json.JSONDecodeError, ValueError):
            logger.warning("reconcile seam %d|%d unparseable; keeping draft", seam.a_index, seam.b_index)
            continue
        translations.update({uid: text for uid, text in improved.items() if text.strip()})
        logger.info("reconciled seam %d|%d (%d units)", seam.a_index, seam.b_index, len(improved))
    return usage


def _max_tokens_for_units(units: Sequence[TextUnit], config: TranslateConfig) -> int:
    """``max_tokens`` for an ad-hoc unit window (the seam pass has no Chunk)."""
    source_tokens = config.estimate_source_tokens(sum(len(u.source) for u in units))
    return min(round(config.estimate_output_tokens(source_tokens) * 2) + len(units) * 30 + 1024, _MAX_OUTPUT_TOKENS)


def _profile_to_json(profile: BookProfile) -> str:
    """Serialize a ``BookProfile`` to a JSON string for the brief cache."""
    return json.dumps({
        "title_en": profile.title_en,
        "description_en": profile.description_en,
        "summary": profile.summary,
        "register": profile.register,
        "personas": [{"name": p.name, "voice": p.voice} for p in profile.personas],
        "terms": [{"source": t.source, "target": t.target, "note": t.note, "locked": t.locked}
                  for t in profile.terms],
        "recurring": list(profile.recurring),
    }, ensure_ascii=False)


def _profile_from_json_str(
    profile_json: str,
    *,
    fallback_title: str,
    fallback_desc: str,
) -> BookProfile:
    """Deserialize a cached ``BookProfile`` JSON string. Degrades on bad JSON."""
    from pancratius.translate.profile import _profile_from_json  # local import avoids circular

    try:
        data = json.loads(profile_json)
    except (json.JSONDecodeError, ValueError):
        logger.warning("cached profile JSON corrupt; building minimal profile")
        data = {}
    return _profile_from_json(
        data,
        fallback_title=fallback_title,
        fallback_desc=fallback_desc,
    )


def _only_requested(returned: Translations, requested: Sequence[UnitId]) -> dict[UnitId, str]:
    wanted = set(requested)
    return {uid: text for uid, text in returned.items() if uid in wanted}


def _ensure_cost(usage: Usage, client: TranslatorClient, config: TranslateConfig) -> Usage:
    """Guarantee a non-None cost so the ``--max-cost`` guard never fails open: if the
    provider omitted ``cost`` from usage, compute it from live pricing and the token
    counts (uses the draft model's pricing — exact for a uniform-model run)."""
    if usage.cost_usd is not None:
        return usage
    pricing = client.fetch_pricing(config.models.draft)
    cost = pricing.cost(usage.prompt_tokens, usage.completion_tokens, usage.cached_tokens)
    return replace(usage, cost_usd=cost)
