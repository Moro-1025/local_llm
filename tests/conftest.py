"""テスト共通のヘルパー。llama-server は respx でモックし、GPU なしで通す。"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

BASE_URL = "http://llama.test"


def sse_body(events: list[dict[str, Any] | str]) -> bytes:
    """辞書(JSON 化する)または文字列のリストから SSE 本文を組み立てる。"""
    lines: list[str] = []
    for ev in events:
        data = ev if isinstance(ev, str) else json.dumps(ev, ensure_ascii=False)
        lines.append(f"data: {data}\n\n")
    return "".join(lines).encode("utf-8")


def sse_response(events: list[dict[str, Any] | str], status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        headers={"content-type": "text/event-stream"},
        content=sse_body(events),
    )


def delta_event(
    content: str | None = None,
    reasoning: str | None = None,
    finish_reason: str | None = None,
    usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    """llama-server が返す chat.completion.chunk 風のイベントを作る。"""
    delta: dict[str, Any] = {}
    if content is not None:
        delta["content"] = content
    if reasoning is not None:
        delta["reasoning_content"] = reasoning
    event: dict[str, Any] = {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "model": "test-model",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    if usage is not None:
        event["usage"] = usage
    return event


@pytest.fixture
def base_url() -> str:
    return BASE_URL
