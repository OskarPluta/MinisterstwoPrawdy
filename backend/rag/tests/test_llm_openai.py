"""OpenAI backend against a mocked HTTP transport (no network, no key needed)."""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx2 as httpx
import openai
import pytest

from rag.llm import ClaimExtraction, LLMRefusal, OpenAILLM, Usage

NOW = datetime(2026, 11, 21, 12, tzinfo=ZoneInfo("Europe/Warsaw"))


def _completion(content: str | None, refusal: str | None = None, finish: str = "stop") -> dict:
    return {
        "id": "chatcmpl-test", "object": "chat.completion", "created": 0, "model": "gpt-test",
        "choices": [{
            "index": 0, "finish_reason": finish,
            "message": {"role": "assistant", "content": content, "refusal": refusal},
        }],
        "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
    }


def _llm(handler) -> tuple[OpenAILLM, list]:
    seen: list = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=handler(request))

    llm = OpenAILLM.__new__(OpenAILLM)
    llm.model, llm.reasoning_effort = "gpt-test", None
    llm.client = openai.OpenAI(
        api_key="test", http_client=httpx.Client(transport=httpx.MockTransport(wrapped)), max_retries=0
    )
    from rag.llm import Usage

    llm.usage = Usage()
    return llm, seen


EXTRACTION = {
    "status": "ok", "clarification_question": None, "injection_detected": False,
    "claims": [{
        "text": "IMGW wydał ostrzeżenie 2. stopnia dla całego Mazowsza",
        "search_queries": ["ostrzeżenie IMGW Mazowsze"], "entities": ["IMGW"],
        "area": "całe Mazowsze", "time_from": "2026-11-21T00:00:00+01:00",
        "time_to": "2026-11-21T23:59:00+01:00", "quantifiers": ["cały"], "checkable": True,
    }],
}


def test_extract_parses_strict_schema_and_counts_usage():
    llm, seen = _llm(lambda r: _completion(json.dumps(EXTRACTION, ensure_ascii=False)))
    out = llm.extract_claims("Czy IMGW <b>wydał</b> ostrzeżenie?", NOW)
    assert isinstance(out, ClaimExtraction) and out.claims[0].area == "całe Mazowsze"
    body = seen[0]
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert "reasoning_effort" not in body
    assert "<b>" not in body["messages"][1]["content"]  # user text escaped
    assert llm.usage.calls == 1 and llm.usage.input_tokens == 120


def test_refusal_and_truncation_raise():
    llm, _ = _llm(lambda r: _completion(None, refusal="I can't help with that."))
    with pytest.raises(LLMRefusal):
        llm.extract_claims("x", NOW)

    llm, _ = _llm(lambda r: _completion('{"status": "ok"', finish="length"))
    with pytest.raises(openai.LengthFinishReasonError):
        llm.extract_claims("x", NOW)


def test_openai_pricing_prefix_and_usage_delta(monkeypatch):
    monkeypatch.delenv("PRAWDOWID_PRICE_IN", raising=False)
    monkeypatch.delenv("PRAWDOWID_PRICE_OUT", raising=False)

    u = Usage()
    u.add("extract", 1000, 500)
    snap = u.snapshot()
    u.add("verify", 2000, 1000)

    delta = u.since(snap)
    assert delta.calls == 1
    assert delta.input_tokens == 2000 and delta.output_tokens == 1000
    assert [c["call"] for c in delta.by_call] == ["verify"]

    # gpt-5.5: $2.50 / 1M in, $10.00 / 1M out -> 2000*2.5/1e6 + 1000*10/1e6 = 0.015
    assert delta.cost_usd("gpt-5.5") == pytest.approx(0.015)
    assert delta.cost_usd("gpt-5.5-2026-01-01") == pytest.approx(0.015)
    assert delta.cost_usd("unknown-model") is None

    monkeypatch.setenv("PRAWDOWID_PRICE_IN", "1.0")
    monkeypatch.setenv("PRAWDOWID_PRICE_OUT", "2.0")
    assert delta.cost_usd("gpt-5.5") == pytest.approx(0.004)
