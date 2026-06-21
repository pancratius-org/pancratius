"""Cover-translation pipeline: recon → generate → QA loop.

Per cover:
  1. Recon: cheap vision call that both FINDS and TRANSLATES every text element on
     the RU cover (Russian + English + role + art-baked flag). This is load-bearing:
     the model cannot miss a string it is later handed verbatim. A missed «в его
     власти» is exactly what a purely-observational recon let slip.
  2. Resolve: each element's authoritative English (override > title pin for the
     title > fixed author > recon's translation; see ``resolve_elements``).
  3. Generate: image-edit call (gemini-3.1-flash-image) handed an EXPLICIT,
     enumerated «russian → english» replacement map. It renders the given strings;
     it does not find-or-translate on its own.
  4. QA: cheap vision call on BOTH images checks for Cyrillic left, artwork
     changes, dropped text, and wrong author.
  5. Steering loop: on failure, a retry edits the PREVIOUS attempt's output (not
     the raw source again) and steering names the specific failing element by its
     English. Editing a mostly-correct image with a targeted fix preserves a good
     attempt instead of re-interpreting the whole cover from scratch each time.

Art-baked text (e.g. book-50's coin emblem «Система дефицита») is attempted; if it
survives the attempt cap it is reported unresolved — without the overlay text having
been degraded by repeated full re-rolls.

Entry: ``translate_cover`` (single cover), ``translate_covers`` (batch).

On re-run: if an .en.png already exists for a cover, QA it FIRST.
PASS → done, no regeneration.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from pancratius.cover.client import (
    CoverClientError,
    GenerationRefusal,
    GenerationResponse,
    VisionResponse,
    api_key_from_env,
    generate_cover,
    vision_text,
)
from pancratius.cover.decrop import DecropReport, decrop_to_source
from pancratius.cover.models import (
    UNRESOLVED_TITLE,
    AttemptRecord,
    CoverResult,
    CoverStatus,
    GenerationCost,
    QaDiscrepancy,
    QaResult,
    QaVerdict,
    ReconResult,
    ResolvedElement,
    ResolvedTitle,
)
from pancratius.cover.prompts import (
    SteeringLevel,
    build_steering,
    generation_prompt,
    qa_prompt,
    recon_prompt,
)
from pancratius.cover.schema import parse_qa, parse_recon, qa_format, recon_format
from pancratius.cover.seed import (
    SeedMap,
    author_only_elements,
    load_seed,
    parse_queue_titles,
    resolve_elements,
    resolve_title,
)

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
INTER_CALL_SLEEP_S = 2.0

# Fallback image-edit model used after a content-filter refusal if the primary
# model refuses again on the one automatic retry. A fal.ai FLUX Kontext model
# via OpenRouter is a reasonable default; set to None to disable the fallback.
DEFAULT_BACKUP_GENERATION_MODEL = "fal-ai/flux-pro/kontext"

# Default paths (mirrors the working script)
DEFAULT_COVERS_DIR = Path.home() / "projects/misc/pancratius-misc/cover-queue"
DEFAULT_QUEUE_MD = DEFAULT_COVERS_DIR / "QUEUE.md"
DEFAULT_BOOKS_ROOT = Path.home() / "projects/misc/pancratius/src/content/books"
DEFAULT_SEED_PATH = (
    Path(__file__).resolve().parent.parent.parent / "docs/scratchpad/seed.json"
)


@dataclass(frozen=True, slots=True)
class CoverTranslateConfig:
    """Run-level knobs for the cover pipeline."""

    output_dir: Path
    covers_dir: Path = DEFAULT_COVERS_DIR
    queue_md: Path = DEFAULT_QUEUE_MD
    books_root: Path = DEFAULT_BOOKS_ROOT
    seed_path: Path = DEFAULT_SEED_PATH
    max_attempts: int = MAX_ATTEMPTS
    inter_call_sleep: float = INTER_CALL_SLEEP_S
    backup_generation_model: str | None = DEFAULT_BACKUP_GENERATION_MODEL

    def __post_init__(self) -> None:
        # The steering loop runs `range(1, max_attempts + 1)`; < 1 would silently
        # generate nothing and report a confusing empty-attempts failure.
        if self.max_attempts < 1:
            raise ValueError(f"max_attempts must be >= 1, got {self.max_attempts}")
        if self.inter_call_sleep < 0:
            raise ValueError(f"inter_call_sleep must be >= 0, got {self.inter_call_sleep}")


def _source_path(covers_dir: Path, book_key: str) -> Path:
    """Return the .ru.png or .ru.jpg cover for a book key.

    Globs the same extensions as discover_books so M3 is not a mismatch.
    """
    for ext in (".ru.png", ".ru.jpg"):
        p = covers_dir / f"{book_key}{ext}"
        if p.exists():
            return p
    # Return the .png path as the canonical "not found" sentinel
    return covers_dir / f"{book_key}.ru.png"


def _run_recon(source: Path, api_key: str) -> tuple[ReconResult, float]:
    """Call the cheap vision model on the RU cover to extract+translate every element.

    Returns (ReconResult, cost_usd). A parse failure degrades to an empty recon;
    the caller then falls back to an author-only replacement map (generation still
    pins the author rather than reverting to translate-it-yourself).
    """
    resp: VisionResponse = vision_text(
        images=[source],
        prompt=recon_prompt(),
        api_key=api_key,
        response_format=recon_format(),
    )
    try:
        recon = parse_recon(resp.text)
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        logger.warning("recon parse failed (%s); using empty result", exc)
        recon = ReconResult(elements=(), displayed_title="", raw_json=resp.text)
    return recon, resp.cost_usd


def _run_qa(source: Path, en_path: Path, *,
             title: ResolvedTitle, api_key: str) -> tuple[QaResult, float]:
    """Call the cheap vision model on both images and return structured QA.

    We pass the response_format schema as a hint but do not require it: some
    vision-model endpoints ignore or mishandle json_schema with multi-image
    input. The prompt itself asks for JSON and _extract_json extracts it from
    a markdown-fenced reply if the model emits one.

    H3: a parse failure FAILS CLOSED — a reply quoting '"verdict": "pass"' could
    be a FAIL reply describing what a pass looks like, so we never infer PASS from
    unparseable text.

    Returns (QaResult, cost_usd).
    """
    resp: VisionResponse = vision_text(
        images=[source, en_path],
        prompt=qa_prompt(title),
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
    edit_base: Path,
    source: Path,
    elements: tuple[ResolvedElement, ...],
    steering: str,
    raw_out: Path,
    final_out: Path,
    api_key: str,
    model: str | None = None,
) -> tuple[GenerationCost, DecropReport]:
    """Generate the EN cover via the image-edit model, then de-crop.

    The model edits ``edit_base`` (the RU source on the first attempt, the previous
    attempt's EN output on a retry — see the loop) but is always de-cropped back to
    ``source`` dimensions, which are ground truth. PIL/decode errors from a bad
    image reply are wrapped in CoverClientError so the caller's except guard suffices.

    Raises ``GenerationRefusal`` (a subclass of CoverClientError) when the model
    declines; the caller handles retry / fallback.
    """
    prompt = generation_prompt(
        elements=elements,
        steering=steering,
    )
    gen_resp: GenerationResponse = generate_cover(edit_base, prompt, api_key, model=model)
    cost = GenerationCost(cost_usd=gen_resp.cost_usd, usage=dict(gen_resp.usage))
    try:
        decrop = decrop_to_source(
            raw_bytes=gen_resp.image_bytes,
            source=source,
            raw_out=raw_out,
            final_out=final_out,
        )
    except Exception as exc:  # PIL/binascii errors on a bad image reply
        raise CoverClientError(f"bad image bytes from generation: {exc}") from exc
    return cost, decrop


def _unlink_if_exists(p: Path) -> None:
    """Remove a file if it exists; no-op otherwise."""
    import contextlib
    with contextlib.suppress(FileNotFoundError):
        p.unlink()


def _snapshot(src: Path, dst: Path) -> bool:
    """Copy ``src`` to ``dst`` (the previous attempt's output, as the next edit base).

    Returns True on success. On failure (missing/unreadable source) ``dst`` is left
    untouched and the caller keeps editing the RU source, never a stale snapshot.
    """
    import shutil
    try:
        shutil.copyfile(src, dst)
    except OSError:
        return False
    return True


def _resolve_title(book_key: str, config: CoverTranslateConfig, seed: SeedMap) -> ResolvedTitle:
    """The single title-to-render decision for this cover (pin lookup + plan)."""
    queue_titles: dict[str, str] = {}
    if config.queue_md.exists():
        queue_titles, _ = parse_queue_titles(config.queue_md)
    return resolve_title(
        book_key, books_root=config.books_root, queue_titles=queue_titles, seed=seed
    )


def _steering_level(attempt_n: int) -> SteeringLevel:
    """FIRM on the first correction; URGENT once a defect has survived a retry.

    ``attempt_n`` is the 1-indexed attempt that just failed; its steering feeds the
    NEXT attempt. attempt 1 → FIRM (first correction), attempt >= 2 → URGENT.
    """
    return SteeringLevel.FIRM if attempt_n == 1 else SteeringLevel.URGENT


def translate_cover(
    book_key: str,
    config: CoverTranslateConfig,
    api_key: str,
) -> CoverResult:
    """Translate one cover through the full recon→generate→QA loop.

    If an existing .en.png is found, QA it first: PASS → done immediately.
    On failure, regenerate with steering addendum, up to config.max_attempts.
    """
    source = _source_path(config.covers_dir, book_key)
    seed: SeedMap = load_seed(config.seed_path)
    title = _resolve_title(book_key, config, seed)

    # Every CoverResult for this cover shares book_key / title / displayed_title;
    # bind them once so each exit point only states what differs.
    def result(
        *,
        status: CoverStatus,
        attempts: tuple[AttemptRecord, ...] = (),
        final_path: Path | None = None,
        raw_path: Path | None = None,
        displayed_title: str | None = None,
        error: str | None = None,
        total_cost_usd: float = 0.0,
        art_baked_leftovers: tuple[str, ...] = (),
    ) -> CoverResult:
        return CoverResult(
            book_key=book_key, status=status, final_path=final_path, raw_path=raw_path,
            attempts=attempts, title=title, displayed_title=displayed_title,
            error=error, total_cost_usd=total_cost_usd,
            art_baked_leftovers=art_baked_leftovers,
        )

    if not source.exists():
        return result(status=CoverStatus.FAIL, error=f"source cover not found: {source}")

    config.output_dir.mkdir(parents=True, exist_ok=True)
    raw_out = config.output_dir / f"{book_key}.raw.png"
    final_out = config.output_dir / f"{book_key}.en.png"
    # The previous attempt's output, used as the edit base for a targeted retry.
    prev_out = config.output_dir / f"{book_key}.prev.png"

    # Step 1: recon — extract AND translate every text element off the RU cover.
    # This is load-bearing: its resolved elements become the generation map.
    logger.info("[%s] recon ...", book_key)
    total_cost = 0.0
    displayed_title: str | None = None
    elements: tuple[ResolvedElement, ...] = author_only_elements()
    try:
        recon, recon_cost = _run_recon(source, api_key)
        total_cost += recon_cost
        displayed_title = recon.displayed_title or None
        resolved = resolve_elements(
            recon.elements, title=title, overrides=dict(seed.overrides)
        )
        # Recon may have read nothing usable; keep the author-only fallback then.
        if resolved:
            elements = resolved
        logger.info(
            "[%s] recon: displayed_title=%r  elements=%d (art_baked=%d)",
            book_key, displayed_title, len(elements),
            sum(1 for e in elements if e.art_baked),
        )
    except CoverClientError as exc:
        logger.warning(
            "[%s] recon failed (%s); using author-only replacement map", book_key, exc
        )

    time.sleep(config.inter_call_sleep)

    # If an existing EN cover exists, QA it first before regenerating.
    if final_out.exists():
        logger.info("[%s] existing EN cover found; QA-ing before regenerating", book_key)
        try:
            qa, qa_cost = _run_qa(source, final_out, title=title, api_key=api_key)
            total_cost += qa_cost
            logger.info("[%s] existing cover QA: %s", book_key, qa.verdict)
            if qa.verdict == QaVerdict.PASS:
                skip = AttemptRecord(  # attempt=0 marks "was already done"
                    attempt=0, qa=qa, generation_cost=GenerationCost(cost_usd=0.0),
                    prompt_steering="",
                )
                return result(
                    status=CoverStatus.OK, attempts=(skip,), final_path=final_out,
                    raw_path=raw_out if raw_out.exists() else None,
                    displayed_title=displayed_title, total_cost_usd=total_cost,
                )
        except CoverClientError as exc:
            logger.warning("[%s] QA of existing cover failed (%s); regenerating", book_key, exc)

    # Entering the regeneration loop — remove stale outputs so a previous failed
    # run's files don't persist on disk looking authoritative.
    _unlink_if_exists(final_out)
    _unlink_if_exists(raw_out)
    _unlink_if_exists(prev_out)

    # Generation + QA steering loop. The first attempt edits the RU source; a retry
    # edits the previous attempt's EN output (``edit_base``) so a mostly-correct
    # attempt is touched up rather than re-interpreted from scratch.
    attempts: list[AttemptRecord] = []
    steering = ""
    last_qa: QaResult | None = None
    edit_base = source

    for attempt_n in range(1, config.max_attempts + 1):
        logger.info("[%s] generate attempt %d/%d (base=%s) ...",
                    book_key, attempt_n, config.max_attempts, edit_base.name)
        try:
            gen_cost, decrop = _run_generation(
                edit_base=edit_base,
                source=source,
                elements=elements,
                steering=steering,
                raw_out=raw_out,
                final_out=final_out,
                api_key=api_key,
            )
        except GenerationRefusal as exc:
            # Content-filter refusal: retry once with the same model, then fall
            # back to the backup model. Refusals are often transient.
            logger.warning("[%s] refusal on attempt %d (%s); retrying once", book_key, attempt_n, exc)
            fallback_model: str | None = None
            try:
                gen_cost, decrop = _run_generation(
                    edit_base=edit_base,
                    source=source,
                    elements=elements,
                    steering=steering,
                    raw_out=raw_out,
                    final_out=final_out,
                    api_key=api_key,
                )
            except GenerationRefusal as exc2:
                if config.backup_generation_model is None:
                    err = f"generation refused twice (no backup model): {exc2}"
                    logger.error("[%s] %s", book_key, err)
                    _unlink_if_exists(prev_out)
                    return result(
                        status=CoverStatus.FAIL, attempts=tuple(attempts),
                        displayed_title=displayed_title, error=err, total_cost_usd=total_cost,
                    )
                fallback_model = config.backup_generation_model
                logger.warning(
                    "[%s] refusal persists; switching to backup model %r",
                    book_key, fallback_model,
                )
                try:
                    gen_cost, decrop = _run_generation(
                        edit_base=edit_base,
                        source=source,
                        elements=elements,
                        steering=steering,
                        raw_out=raw_out,
                        final_out=final_out,
                        api_key=api_key,
                        model=fallback_model,
                    )
                except CoverClientError as exc3:
                    err = f"generation failed after refusal + backup (attempt {attempt_n}): {exc3}"
                    logger.error("[%s] %s", book_key, err)
                    _unlink_if_exists(prev_out)
                    return result(
                        status=CoverStatus.FAIL, attempts=tuple(attempts),
                        displayed_title=displayed_title, error=err, total_cost_usd=total_cost,
                    )
            if fallback_model:
                logger.info("[%s] backup model %r succeeded", book_key, fallback_model)
        except CoverClientError as exc:
            err = f"generation failed (attempt {attempt_n}): {exc}"
            logger.error("[%s] %s", book_key, err)
            _unlink_if_exists(prev_out)
            return result(
                status=CoverStatus.FAIL, attempts=tuple(attempts), displayed_title=displayed_title,
                error=err, total_cost_usd=total_cost,
            )

        total_cost += gen_cost.cost_usd
        logger.info(
            "[%s] generated  raw=%s final=%s decrop_ok=%s  $%.5f",
            book_key, decrop.raw_size, decrop.final_size, decrop.ok, gen_cost.cost_usd,
        )

        time.sleep(config.inter_call_sleep)

        # QA the new output. A QA transport failure is itself a FAIL (fail closed).
        logger.info("[%s] QA attempt %d ...", book_key, attempt_n)
        try:
            qa, qa_cost = _run_qa(source, final_out, title=title, api_key=api_key)
            total_cost += qa_cost
        except CoverClientError as exc:
            logger.warning("[%s] QA call failed (%s); treating as fail", book_key, exc)
            qa = QaResult(
                verdict=QaVerdict.FAIL,
                discrepancies=(QaDiscrepancy(kind="other", description=str(exc)),),
                raw_json="",
            )

        logger.info("[%s] QA verdict: %s  discrepancies=%d",
                    book_key, qa.verdict, len(qa.discrepancies))

        attempts.append(AttemptRecord(
            attempt=attempt_n, qa=qa, generation_cost=gen_cost, prompt_steering=steering,
        ))
        last_qa = qa

        if qa.verdict == QaVerdict.PASS:
            break

        if attempt_n < config.max_attempts:
            steering = build_steering(
                qa.discrepancies, elements=elements, level=_steering_level(attempt_n)
            )
            logger.info("[%s] steering for retry: %s", book_key, steering[:120])
            # Retry edits this attempt's output: a targeted touch-up of the named
            # leftover, preserving the elements already rendered correctly. Snapshot
            # it to prev_out because the next attempt overwrites final_out; if the
            # snapshot fails, fall back to re-editing the RU source.
            edit_base = prev_out if _snapshot(final_out, prev_out) else source
            time.sleep(config.inter_call_sleep)

    passed = last_qa is not None and last_qa.verdict == QaVerdict.PASS

    # prev_out is a retry-only scratch file; it must never persist past the loop.
    _unlink_if_exists(prev_out)

    if passed:
        return result(
            status=CoverStatus.OK,
            attempts=tuple(attempts),
            final_path=final_out,
            raw_path=raw_out,
            displayed_title=displayed_title,
            total_cost_usd=total_cost,
        )

    # Terminal failure: a cover is still usable WITH A CAVEAT only when every
    # remaining discrepancy is untranslated Cyrillic that QA itself saw painted into
    # the artwork (a stubborn coin/emblem/banner, or a faint decorative glyph). We
    # trust QA's structured `in_artwork` judgement — it observes the OUTPUT, so it
    # catches faint baked-in marks the source recon's coarser pass misses, and it
    # reliably tells a caption layer from artwork. An overlay leftover, artwork
    # damage, or any other defect kind is a hard fail: unlink the file.
    remaining: tuple[QaDiscrepancy, ...] = last_qa.discrepancies if last_qa else ()

    art_baked_only = (
        bool(remaining)
        and all(d.kind == "cyrillic_left" and d.in_artwork for d in remaining)
    )

    if art_baked_only:
        leftover_descriptions = tuple(d.description for d in remaining)
        logger.warning(
            "[%s] QA failed after %d attempts but ALL discrepancies are art-baked "
            "(seal/emblem text); keeping cover with caveat. Unresolved: %s",
            book_key, config.max_attempts,
            "; ".join(leftover_descriptions),
        )
        return result(
            status=CoverStatus.OK_WITH_CAVEAT,
            attempts=tuple(attempts),
            final_path=final_out,
            raw_path=raw_out,
            displayed_title=displayed_title,
            total_cost_usd=total_cost,
            art_baked_leftovers=leftover_descriptions,
        )

    unresolved = "; ".join(d.description for d in remaining)
    logger.warning(
        "[%s] still failing after %d attempts: %s", book_key, config.max_attempts, unresolved
    )
    # Hard fail: remove stale outputs — a terminal FAIL must not leave an
    # authoritative-looking file.
    _unlink_if_exists(final_out)
    _unlink_if_exists(raw_out)

    return result(
        status=CoverStatus.FAIL,
        attempts=tuple(attempts),
        displayed_title=displayed_title,
        error=f"QA failed after {config.max_attempts} attempts: {unresolved}",
        total_cost_usd=total_cost,
    )


def discover_books(covers_dir: Path) -> list[str]:
    """Every book-XX with a source cover present, sorted by number."""
    import re
    keys: set[str] = set()
    for p in covers_dir.glob("book-*.ru.*"):
        m = re.match(r"(book-\d+)\.ru\.", p.name)
        if m:
            keys.add(m.group(1))
    return sorted(keys, key=lambda k: int(k.split("-")[1]))


def translate_covers(
    book_keys: list[str],
    config: CoverTranslateConfig,
) -> list[CoverResult]:
    """Translate a list of covers, reporting per-cover outcomes.

    Continues past individual failures so one bad cover never stops the batch.
    """
    api_key = api_key_from_env()
    results: list[CoverResult] = []
    for book_key in book_keys:
        try:
            result = translate_cover(book_key, config, api_key)
        except Exception as exc:  # noqa: BLE001 — one bad cover must not stop the batch
            logger.error("[%s] unexpected exception: %s", book_key, exc)
            result = CoverResult(
                book_key=book_key, status=CoverStatus.FAIL, final_path=None, raw_path=None,
                attempts=(), title=UNRESOLVED_TITLE, displayed_title=None,
                error=f"exception: {exc}", total_cost_usd=0.0,
            )
        results.append(result)
    return results
