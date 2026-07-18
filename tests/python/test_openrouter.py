from __future__ import annotations

from typing import Any

import pytest

from pancratius.openrouter import ChatMessage, Completion, ModelPricing, OpenRouterClient, Usage
from pancratius.translation.text.client import CostCapExceeded, CostCappedClient


def test_chat_completions_disable_reasoning() -> None:
    captured: dict[str, Any] = {}

    class RecordingClient(OpenRouterClient):
        def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
            assert path == "/chat/completions"
            captured.update(payload)
            return {
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {},
            }

    client = RecordingClient(api_key="test")
    client.complete(
        model="test/model",
        messages=[ChatMessage("user", "hello")],
        temperature=0,
        max_tokens=10,
    )

    assert captured["reasoning"] == {"enabled": False}


def test_cost_cap_stops_before_the_next_call() -> None:
    class FakeClient:
        def complete(self, **kwargs: object) -> Completion:
            return Completion(
                text="ok",
                usage=Usage(1, 1, 0, 0.6),
                model=str(kwargs["model"]),
            )

        def fetch_pricing(self, model: str) -> ModelPricing:
            raise AssertionError(f"reported cost should be used for {model}")

    client = CostCappedClient(FakeClient(), 1.0)
    client.complete(
        model="test/model",
        messages=[ChatMessage("user", "hello")],
        temperature=0,
        max_tokens=10,
    )
    client.complete(
        model="test/model",
        messages=[ChatMessage("user", "hello")],
        temperature=0,
        max_tokens=10,
    )

    with pytest.raises(CostCapExceeded, match=r"billed \$1\.2000"):
        client.complete(
            model="test/model",
            messages=[ChatMessage("user", "hello")],
            temperature=0,
            max_tokens=10,
        )
    assert client.spent_usd == 1.2
