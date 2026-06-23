"""HTTP client for image text translation via OpenRouter."""

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

from pancratius.translation.image.models import (
    GENERATION_MODEL,
    GENERATION_RESOLUTION,
    OPENROUTER_URL,
    VISION_MODEL,
    JsonObject,
)

type ResponseFormat = dict[str, Any]

_TIMEOUT = 180.0
_MAX_RETRIES = 3
_RETRY_DELAY = 5.0


class ImageTranslationClientError(RuntimeError):
    """An API call to OpenRouter failed."""


class InsufficientCreditsError(ImageTranslationClientError):
    """OpenRouter rejected the request because the account cannot pay for it."""


class GenerationRefusal(ImageTranslationClientError):
    """The generation API declined to produce an image."""


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
        "X-Title": "image-translate",
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
    return (prompt * 0.5 + completion * 3.0) / 1_000_000


def _looks_like_insufficient_credits(status: int | None, body: str) -> bool:
    if status == 402:
        return True
    normalized = body.casefold()
    return (
        "insufficient credit" in normalized
        or "insufficient balance" in normalized
        or "can only afford" in normalized
        or "not enough credit" in normalized
    )


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
                text = resp.read().decode("utf-8")
                body = json.loads(text)
                if _looks_like_insufficient_credits(resp.status, text):
                    raise InsufficientCreditsError(f"HTTP {resp.status}: {text[:500]}")
                if isinstance(body, dict):
                    return body
                raise ImageTranslationClientError(f"unexpected JSON response: {text[:400]}")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:500]
            if _looks_like_insufficient_credits(exc.code, body):
                raise InsufficientCreditsError(f"HTTP {exc.code}: {body}") from exc
            if exc.code != 429 and not 500 <= exc.code < 600:
                raise ImageTranslationClientError(f"HTTP {exc.code}: {body}") from exc
            last = f"HTTP {exc.code}"
        except (urllib.error.URLError, http.client.HTTPException, ConnectionError, TimeoutError) as exc:
            last = f"transport: {type(exc).__name__}: {exc}"
        except json.JSONDecodeError:
            last = "malformed JSON response"
        attempt += 1
        if attempt >= _MAX_RETRIES:
            raise ImageTranslationClientError(f"exhausted {_MAX_RETRIES} retries: {last}")
        time.sleep(_RETRY_DELAY * attempt)


def _is_refusal(body: JsonObject) -> bool:
    choices = body.get("choices") or []
    if choices:
        choice = choices[0]
        if choice.get("finish_reason") == "content_filter":
            return True
        msg = choice.get("message") or {}
        if msg.get("refusal"):
            return True
    err = body.get("error") or {}
    if isinstance(err, dict):
        code = str(err.get("code") or "")
        message = str(err.get("message") or "").lower()
        return code in ("content_filter", "safety") or "safety" in message or "policy" in message
    return False


def _extract_image(body: JsonObject, usage: JsonObject) -> GenerationResponse:
    if _is_refusal(body):
        raise GenerationRefusal("content-filter refusal in generation response")

    choices = body.get("choices") or []
    if not choices:
        raise ImageTranslationClientError(f"no choices in generation response: {json.dumps(body)[:400]}")
    msg = choices[0].get("message") or {}

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
    raise ImageTranslationClientError(
        f"no image in generation response; message keys: {list(msg.keys())}"
    )


def generate_image_translation(
    source: Path,
    prompt: str,
    api_key: str,
    *,
    model: str | None = None,
) -> GenerationResponse:
    """Send an image plus instructions, get back an edited image."""
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
    """Vision-text call via the cheap VISION_MODEL."""
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
        raise ImageTranslationClientError(f"no choices in vision response: {json.dumps(body)[:400]}")
    msg = choices[0].get("message") or {}
    text = msg.get("content") or ""
    if not isinstance(text, str):
        text = ""
    return VisionResponse(text=text, cost_usd=_cost_from_usage(usage), usage=usage)


def api_key_from_env() -> str:
    key = os.environ.get("OPENROUTER_API_KEY") or ""
    if not key:
        raise ValueError(
            "OPENROUTER_API_KEY is not set; export it before running image translation."
        )
    return key
