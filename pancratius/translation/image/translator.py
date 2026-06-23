"""Generic image text translation engine."""

from __future__ import annotations

import json
import logging
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from pancratius.translation.image.client import (
    GenerationRefusal,
    GenerationResponse,
    ImageTranslationClientError,
    InsufficientCreditsError,
    VisionResponse,
    generate_image_translation,
    vision_text,
)
from pancratius.translation.image.decrop import DecropReport, decrop_to_source
from pancratius.translation.image.models import (
    AttemptRecord,
    DetectedText,
    ExactText,
    ExpectedText,
    GenerationCost,
    ImageReconResult,
    ImageTranslationJob,
    ImageTranslationResult,
    ImageTranslationStatus,
    NormalizedText,
    QaDiscrepancy,
    QaResult,
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
    recon_prompt,
)
from pancratius.translation.image.schema import parse_qa, parse_recon, qa_format, recon_format

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
INTER_CALL_SLEEP_S = 2.0
DEFAULT_BACKUP_GENERATION_MODEL = "fal-ai/flux-pro/kontext"


@dataclass(frozen=True, slots=True)
class ImageTranslationConfig:
    """Run-level knobs for the image translation engine."""

    max_attempts: int = MAX_ATTEMPTS
    inter_call_sleep: float = INTER_CALL_SLEEP_S
    backup_generation_model: str | None = DEFAULT_BACKUP_GENERATION_MODEL
    replace_existing: bool = False

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError(f"max_attempts must be >= 1, got {self.max_attempts}")
        if self.inter_call_sleep < 0:
            raise ValueError(f"inter_call_sleep must be >= 0, got {self.inter_call_sleep}")


@dataclass(frozen=True, slots=True)
class _ExpectedAssignment:
    expected: ExpectedText
    indices: tuple[int, ...]
    rank: int


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _expected_source_hint(expected: ExpectedText) -> str | None:
    for selector in expected.selectors:
        if isinstance(selector, (ExactText, NormalizedText)):
            return selector.source
    return None


def _selector_matches(element: DetectedText, selector: object) -> bool:
    if isinstance(selector, ExactText):
        return element.source == selector.source
    if isinstance(selector, NormalizedText):
        return _normalize_text(element.source) == _normalize_text(selector.source)
    if isinstance(selector, RoleSelector):
        return element.role is selector.role
    return False


def _override_matches(element: DetectedText, override: TextOverride) -> bool:
    return _selector_matches(element, override.selector)


def _selector_rank(element: DetectedText, selector: object) -> int | None:
    if isinstance(selector, ExactText) and _selector_matches(element, selector):
        return 0
    if isinstance(selector, NormalizedText) and _selector_matches(element, selector):
        return 1
    if isinstance(selector, RoleSelector) and _selector_matches(element, selector):
        return 2
    return None


def _source_selector_rank(source: str, selector: object) -> int | None:
    if isinstance(selector, ExactText) and source == selector.source:
        return 0
    if isinstance(selector, NormalizedText) and _normalize_text(source) == _normalize_text(selector.source):
        return 1
    return None


def _expected_rank(element: DetectedText, expected: ExpectedText) -> int | None:
    ranks = [
        rank for selector in expected.selectors
        if (rank := _selector_rank(element, selector)) is not None
    ]
    return min(ranks) if ranks else None


def _role_hint(expected: ExpectedText) -> TextRole:
    for selector in expected.selectors:
        if isinstance(selector, RoleSelector):
            return selector.role
    return TextRole.OTHER


def _matching_override(
    element: DetectedText,
    overrides: tuple[TextOverride, ...],
) -> TextOverride | None:
    for selector_type in (ExactText, NormalizedText):
        for override in overrides:
            if isinstance(override.selector, selector_type) and _override_matches(element, override):
                return override
    return None


def _expected_candidates(
    elements: tuple[DetectedText, ...],
    expected: ExpectedText,
    used_elements: set[int],
) -> list[_ExpectedAssignment]:
    candidates: list[_ExpectedAssignment] = []
    for start, element in enumerate(elements):
        if start in used_elements or not element.source.strip():
            continue
        sources: list[str] = []
        indices: list[int] = []
        for index in range(start, len(elements)):
            if index in used_elements:
                break
            current = elements[index]
            if not current.source.strip():
                break
            sources.append(current.source)
            indices.append(index)
            combined = " ".join(sources)
            for selector in expected.selectors:
                rank = _source_selector_rank(combined, selector)
                if rank is not None:
                    candidates.append(_ExpectedAssignment(expected, tuple(indices), rank))
        rank = _expected_rank(element, expected)
        if rank is not None:
            candidates.append(_ExpectedAssignment(expected, (start,), rank))
    return candidates


def _assign_expected(
    elements: tuple[DetectedText, ...],
    expected_text: tuple[ExpectedText, ...],
) -> dict[int, _ExpectedAssignment]:
    """Assign each expected semantic text item to at most one detected element."""
    assigned: dict[int, _ExpectedAssignment] = {}
    used_elements: set[int] = set()
    for expected in expected_text:
        candidates = _expected_candidates(elements, expected, used_elements)
        if not candidates:
            continue
        assignment = min(candidates, key=lambda candidate: (candidate.rank, candidate.indices[0], len(candidate.indices)))
        assigned[assignment.indices[0]] = assignment
        used_elements.update(assignment.indices)
    return assigned


def _matched_expected_ids(expected_by_element: dict[int, _ExpectedAssignment]) -> set[int]:
    return {id(assignment.expected) for assignment in expected_by_element.values()}


def _synthetic_text_for_expected(expected: ExpectedText) -> ResolvedText:
    source = _expected_source_hint(expected) or ""
    return ResolvedText(
        role=_role_hint(expected),
        source=source,
        target=expected.target,
        embedded=False,
        rule=expected,
    )


def _resolved_text_for_assignment(
    elements: tuple[DetectedText, ...],
    assignment: _ExpectedAssignment,
) -> ResolvedText:
    grouped = tuple(elements[index] for index in assignment.indices)
    return ResolvedText(
        role=grouped[0].role if grouped else _role_hint(assignment.expected),
        source="\n".join(element.source for element in grouped),
        target=assignment.expected.target,
        embedded=any(element.embedded for element in grouped),
        rule=assignment.expected,
    )


def resolve_texts(
    elements: tuple[DetectedText, ...],
    expected_text: tuple[ExpectedText, ...],
    *,
    overrides: tuple[TextOverride, ...] = (),
    synthesize_expected: bool = True,
) -> tuple[ResolvedText, ...]:
    """Resolve recon text with provider image contracts.

    Overrides are source-keyed and only apply to detected text. Expected text may
    be synthesized when recon misses it, because it is the provider's explicit
    assertion about text that belongs in this image.
    """
    resolved: list[ResolvedText] = []
    expected_by_element = _assign_expected(elements, expected_text)
    matched = _matched_expected_ids(expected_by_element)
    consumed_expected_indices = {
        index for assignment in expected_by_element.values()
        for index in assignment.indices[1:]
    }
    for index, element in enumerate(elements):
        if not element.source.strip():
            continue
        if index in consumed_expected_indices:
            continue
        assignment = expected_by_element.get(index)
        if assignment is not None and len(assignment.indices) > 1:
            resolved.append(_resolved_text_for_assignment(elements, assignment))
            continue
        override = _matching_override(element, overrides)
        expected = assignment.expected if assignment is not None else None
        rule = override or expected
        resolved.append(
            ResolvedText(
                role=element.role,
                source=element.source,
                target=rule.target if rule else element.suggested_target,
                embedded=element.embedded,
                rule=rule,
            )
        )
    if synthesize_expected:
        for expected in expected_text:
            if id(expected) not in matched:
                resolved.append(_synthetic_text_for_expected(expected))
    return tuple(resolved)


def _safe_stem(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", key).strip("-") or "image"


def _prev_path(job: ImageTranslationJob, raw_out: Path) -> Path:
    return raw_out.with_name(f"{_safe_stem(job.key)}.prev.png")


def _staged_path(path: Path, _key: str) -> Path:
    return path.with_name(f"{_safe_stem(path.stem)}.replace{path.suffix}")


def _unlink_if_exists(p: Path) -> None:
    import contextlib
    with contextlib.suppress(FileNotFoundError):
        p.unlink()


def _snapshot(src: Path, dst: Path) -> bool:
    try:
        shutil.copyfile(src, dst)
    except OSError:
        return False
    return True


def _commit_staged(staged: Path, target: Path) -> None:
    if staged != target:
        staged.replace(target)


def _run_recon(job: ImageTranslationJob, api_key: str) -> tuple[ImageReconResult, float]:
    resp: VisionResponse = vision_text(
        images=[job.source_image],
        prompt=recon_prompt(job),
        api_key=api_key,
        response_format=recon_format(),
    )
    try:
        recon = parse_recon(resp.text)
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        logger.warning("recon parse failed (%s); using empty result", exc)
        recon = ImageReconResult(elements=(), primary_text="", raw_json=resp.text)
    return recon, resp.cost_usd


def _run_qa(
    job: ImageTranslationJob,
    target_path: Path,
    elements: tuple[ResolvedText, ...],
    api_key: str,
) -> tuple[QaResult, float]:
    resp: VisionResponse = vision_text(
        images=[job.source_image, target_path],
        prompt=qa_prompt(job=job, elements=elements),
        api_key=api_key,
        response_format=qa_format(),
    )
    logger.debug("QA raw: %.200s", resp.text)
    try:
        qa = parse_qa(resp.text)
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        logger.warning("QA parse failed (%s); raw: %.200s", exc, resp.text)
        qa = QaResult(
            verdict=QaVerdict.FAIL,
            discrepancies=(QaDiscrepancy(kind="other", description="QA response could not be parsed"),),
            raw_json=resp.text,
        )
    return qa, resp.cost_usd


def _run_generation(
    *,
    job: ImageTranslationJob,
    edit_base: Path,
    elements: tuple[ResolvedText, ...],
    steering: str,
    raw_out: Path,
    final_out: Path,
    api_key: str,
    model: str | None = None,
) -> tuple[GenerationCost, DecropReport]:
    prompt = generation_prompt(job=job, elements=elements, steering=steering)
    gen_resp: GenerationResponse = generate_image_translation(edit_base, prompt, api_key, model=model)
    cost = GenerationCost(cost_usd=gen_resp.cost_usd, usage=dict(gen_resp.usage))
    try:
        decrop = decrop_to_source(
            raw_bytes=gen_resp.image_bytes,
            source=job.source_image,
            raw_out=raw_out,
            final_out=final_out,
        )
    except Exception as exc:
        raise ImageTranslationClientError(f"bad image bytes from generation: {exc}") from exc
    return cost, decrop


def _steering_level(attempt_n: int) -> SteeringLevel:
    return SteeringLevel.FIRM if attempt_n == 1 else SteeringLevel.URGENT


class ImageTextTranslator:
    """Translate visible text in images using a provider-supplied job."""

    def __init__(self, *, config: ImageTranslationConfig, api_key: str) -> None:
        self.config = config
        self.api_key = api_key

    def translate(self, job: ImageTranslationJob) -> ImageTranslationResult:
        source = job.source_image
        raw_out = job.raw_output()
        final_out = job.target_image
        prev_out = _prev_path(job, raw_out)

        def result(
            *,
            status: ImageTranslationStatus,
            attempts: tuple[AttemptRecord, ...] = (),
            final_path: Path | None = None,
            raw_path: Path | None = None,
            primary_text: str | None = None,
            error: str | None = None,
            total_cost_usd: float = 0.0,
            embedded_leftovers: tuple[str, ...] = (),
        ) -> ImageTranslationResult:
            return ImageTranslationResult(
                key=job.key,
                status=status,
                final_path=final_path,
                raw_path=raw_path,
                attempts=attempts,
                primary_text=primary_text,
                error=error,
                total_cost_usd=total_cost_usd,
                metadata=dict(job.metadata),
                embedded_leftovers=embedded_leftovers,
            )

        if not source.exists():
            return result(status=ImageTranslationStatus.FAIL, error=f"source image not found: {source}")

        final_out.parent.mkdir(parents=True, exist_ok=True)
        raw_out.parent.mkdir(parents=True, exist_ok=True)
        had_existing_target = final_out.exists()

        logger.info("[%s] recon ...", job.key)
        total_cost = 0.0
        primary_text: str | None = None
        try:
            recon, recon_cost = _run_recon(job, self.api_key)
            total_cost += recon_cost
            primary_text = recon.primary_text or None
            elements = resolve_texts(
                recon.elements,
                job.expected_text,
                overrides=job.overrides,
            )
            logger.info(
                "[%s] recon: primary_text=%r elements=%d (embedded=%d)",
                job.key,
                primary_text,
                len(elements),
                sum(1 for e in elements if e.embedded),
            )
        except InsufficientCreditsError:
            raise
        except ImageTranslationClientError as exc:
            logger.warning("[%s] recon failed (%s); using expected-text fallback", job.key, exc)
            elements = resolve_texts((), job.expected_text, overrides=job.overrides)

        time.sleep(self.config.inter_call_sleep)

        if final_out.exists():
            logger.info("[%s] existing target image found; QA-ing before regenerating", job.key)
            try:
                qa, qa_cost = _run_qa(job, final_out, elements, self.api_key)
                total_cost += qa_cost
                logger.info("[%s] existing target QA: %s", job.key, qa.verdict)
                if qa.verdict == QaVerdict.PASS:
                    skip = AttemptRecord(
                        attempt=0,
                        qa=qa,
                        generation_cost=GenerationCost(cost_usd=0.0),
                        prompt_steering="",
                    )
                    return result(
                        status=ImageTranslationStatus.OK,
                        attempts=(skip,),
                        final_path=final_out,
                        raw_path=raw_out if raw_out.exists() else None,
                        primary_text=primary_text,
                        total_cost_usd=total_cost,
                    )
                skip = AttemptRecord(
                    attempt=0,
                    qa=qa,
                    generation_cost=GenerationCost(cost_usd=0.0),
                    prompt_steering="",
                )
                if not self.config.replace_existing:
                    return result(
                        status=ImageTranslationStatus.FAIL,
                        attempts=(skip,),
                        primary_text=primary_text,
                        error="existing target failed QA; pass --replace to regenerate it",
                        total_cost_usd=total_cost,
                    )
            except InsufficientCreditsError:
                raise
            except ImageTranslationClientError as exc:
                if not self.config.replace_existing:
                    return result(
                        status=ImageTranslationStatus.FAIL,
                        primary_text=primary_text,
                        error=f"existing target QA failed; pass --replace to regenerate it: {exc}",
                        total_cost_usd=total_cost,
                    )
                logger.warning("[%s] QA of existing target failed (%s); regenerating", job.key, exc)

        write_final = _staged_path(final_out, job.key) if had_existing_target else final_out
        write_raw = _staged_path(raw_out, job.key) if had_existing_target else raw_out
        _unlink_if_exists(write_final)
        _unlink_if_exists(write_raw)
        _unlink_if_exists(prev_out)

        def cleanup_write_outputs() -> None:
            _unlink_if_exists(write_final)
            _unlink_if_exists(write_raw)
            _unlink_if_exists(prev_out)

        attempts: list[AttemptRecord] = []
        steering = job.steering_hint
        last_qa: QaResult | None = None
        edit_base = source

        for attempt_n in range(1, self.config.max_attempts + 1):
            logger.info(
                "[%s] generate attempt %d/%d (base=%s) ...",
                job.key,
                attempt_n,
                self.config.max_attempts,
                edit_base.name,
            )
            try:
                gen_cost, decrop = _run_generation(
                    job=job,
                    edit_base=edit_base,
                    elements=elements,
                    steering=steering,
                    raw_out=write_raw,
                    final_out=write_final,
                    api_key=self.api_key,
                )
            except InsufficientCreditsError:
                cleanup_write_outputs()
                raise
            except GenerationRefusal as exc:
                logger.warning("[%s] refusal on attempt %d (%s); retrying once", job.key, attempt_n, exc)
                fallback_model: str | None = None
                try:
                    gen_cost, decrop = _run_generation(
                        job=job,
                        edit_base=edit_base,
                        elements=elements,
                        steering=steering,
                        raw_out=write_raw,
                        final_out=write_final,
                        api_key=self.api_key,
                    )
                except InsufficientCreditsError:
                    cleanup_write_outputs()
                    raise
                except GenerationRefusal as exc2:
                    if self.config.backup_generation_model is None:
                        err = f"generation refused twice (no backup model): {exc2}"
                        logger.error("[%s] %s", job.key, err)
                        cleanup_write_outputs()
                        return result(
                            status=ImageTranslationStatus.FAIL,
                            attempts=tuple(attempts),
                            primary_text=primary_text,
                            error=err,
                            total_cost_usd=total_cost,
                        )
                    fallback_model = self.config.backup_generation_model
                    logger.warning("[%s] refusal persists; switching to backup model %r", job.key, fallback_model)
                    try:
                        gen_cost, decrop = _run_generation(
                            job=job,
                            edit_base=edit_base,
                            elements=elements,
                            steering=steering,
                            raw_out=write_raw,
                            final_out=write_final,
                            api_key=self.api_key,
                            model=fallback_model,
                        )
                    except InsufficientCreditsError:
                        cleanup_write_outputs()
                        raise
                    except ImageTranslationClientError as exc3:
                        err = f"generation failed after refusal + backup (attempt {attempt_n}): {exc3}"
                        logger.error("[%s] %s", job.key, err)
                        cleanup_write_outputs()
                        return result(
                            status=ImageTranslationStatus.FAIL,
                            attempts=tuple(attempts),
                            primary_text=primary_text,
                            error=err,
                            total_cost_usd=total_cost,
                        )
                if fallback_model:
                    logger.info("[%s] backup model %r succeeded", job.key, fallback_model)
            except ImageTranslationClientError as exc:
                err = f"generation failed (attempt {attempt_n}): {exc}"
                logger.error("[%s] %s", job.key, err)
                cleanup_write_outputs()
                return result(
                    status=ImageTranslationStatus.FAIL,
                    attempts=tuple(attempts),
                    primary_text=primary_text,
                    error=err,
                    total_cost_usd=total_cost,
                )

            total_cost += gen_cost.cost_usd
            logger.info(
                "[%s] generated raw=%s final=%s decrop_ok=%s $%.5f",
                job.key,
                decrop.raw_size,
                decrop.final_size,
                decrop.ok,
                gen_cost.cost_usd,
            )

            time.sleep(self.config.inter_call_sleep)

            logger.info("[%s] QA attempt %d ...", job.key, attempt_n)
            try:
                qa, qa_cost = _run_qa(job, write_final, elements, self.api_key)
                total_cost += qa_cost
            except InsufficientCreditsError:
                cleanup_write_outputs()
                raise
            except ImageTranslationClientError as exc:
                logger.warning("[%s] QA call failed (%s); treating as fail", job.key, exc)
                qa = QaResult(
                    verdict=QaVerdict.FAIL,
                    discrepancies=(QaDiscrepancy(kind="other", description=str(exc)),),
                    raw_json="",
                )

            logger.info("[%s] QA verdict: %s discrepancies=%d", job.key, qa.verdict, len(qa.discrepancies))
            attempts.append(
                AttemptRecord(
                    attempt=attempt_n,
                    qa=qa,
                    generation_cost=gen_cost,
                    prompt_steering=steering,
                )
            )
            last_qa = qa

            if qa.verdict == QaVerdict.PASS:
                break

            if attempt_n < self.config.max_attempts:
                steering = build_steering(
                    qa.discrepancies,
                    elements=elements,
                    level=_steering_level(attempt_n),
                )
                logger.info("[%s] steering for retry: %s", job.key, steering[:120])
                edit_base = prev_out if _snapshot(write_final, prev_out) else source
                time.sleep(self.config.inter_call_sleep)

        passed = last_qa is not None and last_qa.verdict == QaVerdict.PASS
        _unlink_if_exists(prev_out)

        if passed:
            _commit_staged(write_raw, raw_out)
            _commit_staged(write_final, final_out)
            return result(
                status=ImageTranslationStatus.OK,
                attempts=tuple(attempts),
                final_path=final_out,
                raw_path=raw_out,
                primary_text=primary_text,
                total_cost_usd=total_cost,
            )

        remaining: tuple[QaDiscrepancy, ...] = last_qa.discrepancies if last_qa else ()
        embedded_only = (
            bool(remaining)
            and all(d.kind in {"source_text_left", "cyrillic_left"} and d.embedded for d in remaining)
        )

        if job.allow_embedded_text_caveat and embedded_only:
            _commit_staged(write_raw, raw_out)
            _commit_staged(write_final, final_out)
            leftover_descriptions = tuple(d.description for d in remaining)
            logger.warning(
                "[%s] QA failed after %d attempts but all discrepancies are embedded; keeping with caveat. Unresolved: %s",
                job.key,
                self.config.max_attempts,
                "; ".join(leftover_descriptions),
            )
            return result(
                status=ImageTranslationStatus.OK_WITH_CAVEAT,
                attempts=tuple(attempts),
                final_path=final_out,
                raw_path=raw_out,
                primary_text=primary_text,
                total_cost_usd=total_cost,
                embedded_leftovers=leftover_descriptions,
            )

        unresolved = "; ".join(d.description for d in remaining)
        logger.warning("[%s] still failing after %d attempts: %s", job.key, self.config.max_attempts, unresolved)
        cleanup_write_outputs()

        return result(
            status=ImageTranslationStatus.FAIL,
            attempts=tuple(attempts),
            primary_text=primary_text,
            error=f"QA failed after {self.config.max_attempts} attempts: {unresolved}",
            total_cost_usd=total_cost,
        )


def translate_image(
    job: ImageTranslationJob,
    config: ImageTranslationConfig,
    api_key: str,
) -> ImageTranslationResult:
    """Translate one image through recon -> generate -> QA."""
    return ImageTextTranslator(config=config, api_key=api_key).translate(job)
