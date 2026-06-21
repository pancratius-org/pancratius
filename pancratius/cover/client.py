"""HTTP client for the cover pipeline: generation call (image in → image out)
and vision text calls (recon, QA) via OpenRouter.

Uses urllib (standard library), mirroring pancratius/translate/client.py.
Pillow is a base dependency; no extra HTTP library.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pancratius.cover.models import (
    GENERATION_MODEL,
    GENERATION_RESOLUTION,
    OPENROUTER_URL,
    VISION_MODEL,
    JsonObject,
)

type ResponseFormat = dict[str, Any]

# Fallback cost when OpenRouter omits usage.cost (~$0.067 per image at 1K)
_FALLBACK_GENERATION_COST = 0.068

_TIMEOUT = 180.0
_MAX_RETRIES = 3
_RETRY_DELAY = 5.0


class CoverClientError(RuntimeError):
    """An API call to OpenRouter failed."""


class GenerationRefusal(CoverClientError):
    """The generation API declined to produce an image (content-filter refusal).

    Distinct from a transport error: the server responded but chose not to
    generate.  The pipeline retries once (often transient), then falls back to
    a backup model.
    """


@dataclass(frozen=True, slots=True)
class GenerationResponse:
    image_bytes: bytes
    cost_usd: float
    usage: JsonObject


@dataclass(frozen=True, slots=True)
class VisionResponse:
    text: str
    cost_usd: float
    usage: JsonObject


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/pancratius",
        "X-Title": "cover-translate",
    }


def _mime(path: Path) -> str:
    return "image/png" if path.suffix.lower() == ".png" else "image/jpeg"


def _b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("utf-8")


def _cost_from_usage(usage: JsonObject) -> float:
    if cost := usage.get("cost"):
        return float(cost)
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    # Text-rate fallback; image tokens are separately billed, so this undercounts.
    return (prompt * 0.5 + completion * 3.0) / 1_000_000


def _post(payload: JsonObject, api_key: str) -> JsonObject:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        OPENROUTER_URL, data=data, headers=_headers(api_key), method="POST"
    )
    attempt = 0
    last = "?"
    while True:
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Retry 5xx (server errors) and 429 (rate limit); fail fast on all others.
            if exc.code != 429 and not 500 <= exc.code < 600:
                body = exc.read().decode("utf-8", "replace")[:500]
                raise CoverClientError(f"HTTP {exc.code}: {body}") from exc
            last = f"HTTP {exc.code}"
        except (urllib.error.URLError, http.client.HTTPException, ConnectionError, TimeoutError) as exc:
            last = f"transport: {type(exc).__name__}: {exc}"
        except json.JSONDecodeError:
            last = "malformed JSON response"
        attempt += 1
        if attempt >= _MAX_RETRIES:
            raise CoverClientError(f"exhausted {_MAX_RETRIES} retries: {last}")
        time.sleep(_RETRY_DELAY * attempt)


def _is_refusal(body: JsonObject) -> bool:
    """True when the API response indicates a content-filter refusal.

    OpenRouter surfaces refusals in several places: a ``finish_reason`` of
    ``"content_filter"``; a ``refusal`` field on the message; or an empty
    choices list combined with an error mentioning safety/policy.
    """
    choices = body.get("choices") or []
    if choices:
        choice = choices[0]
        if choice.get("finish_reason") == "content_filter":
            return True
        msg = choice.get("message") or {}
        if msg.get("refusal"):
            return True
    # Some providers return no choices and an error object instead.
    err = body.get("error") or {}
    if isinstance(err, dict):
        code = str(err.get("code") or "")
        message = str(err.get("message") or "").lower()
        if code in ("content_filter", "safety") or "safety" in message or "policy" in message:
            return True
    return False


def _extract_image(body: JsonObject, usage: JsonObject) -> GenerationResponse:
    """Extract the image bytes from a successful generation response body.

    Raises ``GenerationRefusal`` when the model declined; raises
    ``CoverClientError`` when the response is otherwise malformed.
    """
    if _is_refusal(body):
        raise GenerationRefusal("content-filter refusal in generation response")

    choices = body.get("choices") or []
    if not choices:
        raise CoverClientError(f"no choices in generation response: {json.dumps(body)[:400]}")
    msg = choices[0].get("message") or {}

    # Image may appear in msg.images[] or in msg.content[].image_url
    for img_entry in msg.get("images") or []:
        url = img_entry.get("image_url", {}).get("url", "") if isinstance(img_entry, dict) else ""
        if url.startswith("data:"):
            return GenerationResponse(
                image_bytes=base64.b64decode(url.split(",", 1)[1]),
                cost_usd=_cost_from_usage(usage),
                usage=usage,
            )
    content = msg.get("content") or ""
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "image_url":
                url = (block.get("image_url") or {}).get("url") or ""
                if url.startswith("data:"):
                    return GenerationResponse(
                        image_bytes=base64.b64decode(url.split(",", 1)[1]),
                        cost_usd=_cost_from_usage(usage),
                        usage=usage,
                    )
    raise CoverClientError(
        f"no image in generation response; message keys: {list(msg.keys())}"
    )


def generate_cover(
    source: Path,
    prompt: str,
    api_key: str,
    *,
    model: str | None = None,
) -> GenerationResponse:
    """Fused vision call: send the RU cover, get back an EN edited cover.

    Uses ``model`` when provided, otherwise the default GENERATION_MODEL
    (gemini-3.1-flash-image). Raises ``GenerationRefusal`` when the model
    declines due to content filtering (callers handle retry / fallback).
    """
    payload: JsonObject = {
        "model": model or GENERATION_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{_mime(source)};base64,{_b64(source)}"},
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "modalities": ["image", "text"],
        "resolution": GENERATION_RESOLUTION,
    }
    body = _post(payload, api_key)
    usage: JsonObject = body.get("usage") or {}
    return _extract_image(body, usage)


def vision_text(
    *,
    images: list[Path],
    prompt: str,
    api_key: str,
    response_format: ResponseFormat | None = None,
) -> VisionResponse:
    """Vision-text call via the cheap VISION_MODEL.

    ``images`` is a list of local image paths to attach (1 for recon, 2 for QA).
    Returns text content + cost.
    """
    content: list[JsonObject] = [
        {
            "type": "image_url",
            "image_url": {"url": f"data:{_mime(p)};base64,{_b64(p)}"},
        }
        for p in images
    ]
    content.append({"type": "text", "text": prompt})

    payload: JsonObject = {
        "model": VISION_MODEL,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 1024,
        "temperature": 0.0,
    }
    if response_format is not None:
        payload["response_format"] = response_format

    body = _post(payload, api_key)
    usage: JsonObject = body.get("usage") or {}
    choices = body.get("choices") or []
    if not choices:
        raise CoverClientError(f"no choices in vision response: {json.dumps(body)[:400]}")
    msg = choices[0].get("message") or {}
    text = msg.get("content") or ""
    if not isinstance(text, str):
        text = ""
    return VisionResponse(text=text, cost_usd=_cost_from_usage(usage), usage=usage)


def api_key_from_env() -> str:
    """Read OPENROUTER_API_KEY from the environment, raise ValueError if absent."""
    key = os.environ.get("OPENROUTER_API_KEY") or ""
    if not key:
        raise ValueError(
            "OPENROUTER_API_KEY is not set; export it before running cover translate."
        )
    return key
