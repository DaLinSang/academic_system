"""Provider-specific authentication tests for the shared LLM client."""

from __future__ import annotations

from typing import Any

import openai
import pytest

import app.llm.client as client_module
from app.core.config import LLMSettings
from app.llm.client import OpenAICompatibleLLM


def test_openai_client_forwards_optional_default_headers(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, Any] = {}

    def fake_openai(**kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(openai, "OpenAI", fake_openai)

    OpenAICompatibleLLM(
        api_key="test-key",
        base_url="https://example.test/v1",
        model="test-model",
        default_headers={"api-key": "test-key"},
    )

    assert captured["default_headers"] == {"api-key": "test-key"}


@pytest.mark.parametrize(
    ("provider", "expected_headers"),
    [
        ("hku", {"api-key": "test-key"}),
        ("deepseek", None),
    ],
)
def test_get_llm_selects_provider_specific_authentication(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    expected_headers: dict[str, str] | None,
):
    captured: dict[str, Any] = {}

    def fake_resolve(self: LLMSettings, selected_provider: str | None = None):
        assert selected_provider == provider
        return "test-key", "https://example.test/v1", "test-model"

    class FakeOpenAICompatibleLLM:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(LLMSettings, "resolve", fake_resolve)
    monkeypatch.setattr(client_module, "OpenAICompatibleLLM", FakeOpenAICompatibleLLM)

    client_module.get_llm(provider=provider, force_new=True)

    assert captured["default_headers"] == expected_headers
