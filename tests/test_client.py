"""LlamaClient のテスト: SSE パース、thinking 分離、エラー変換。"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from local_llm.client import LlamaClient, LlamaClientError, ThinkTagSplitter, iter_sse_data
from local_llm.types import ChatChunk, Message

from conftest import BASE_URL, delta_event, sse_response


# ---------------------------------------------------------------- ThinkTagSplitter


def feed_all(parts: list[str]) -> tuple[str, str]:
    splitter = ThinkTagSplitter()
    content, reasoning = "", ""
    for p in parts:
        c, r = splitter.feed(p)
        content += c
        reasoning += r
    c, r = splitter.flush()
    return content + c, reasoning + r


def test_splitter_no_tags_passes_through():
    assert feed_all(["こんにちは", "、世界"]) == ("こんにちは、世界", "")


def test_splitter_separates_think_block():
    assert feed_all(["<think>考え中</think>答え"]) == ("答え", "考え中")


def test_splitter_handles_tag_split_across_chunks():
    # タグがチャンク境界で "<th" / "ink>" のように分断されても正しく分ける
    parts = ["<th", "ink>推", "論</th", "ink>", "本文"]
    assert feed_all(parts) == ("本文", "推論")


def test_splitter_partial_tag_at_end_is_flushed_as_content():
    # "<" で終わっただけで実際はタグでなかった場合、flush で本文として出る
    assert feed_all(["a <", "b"]) == ("a <b", "")


def test_splitter_unclosed_think_goes_to_reasoning():
    assert feed_all(["<think>途中で切れた"]) == ("", "途中で切れた")


# ---------------------------------------------------------------- iter_sse_data


async def _lines(text: str):
    for line in text.split("\n"):
        yield line


async def collect(agen):
    return [x async for x in agen]


async def test_iter_sse_data_basic_and_done():
    text = "data: {\"a\":1}\n\ndata: [DONE]\n\n"
    assert await collect(iter_sse_data(_lines(text))) == ['{"a":1}', "[DONE]"]


async def test_iter_sse_data_joins_multiline_and_skips_comments():
    text = ": keep-alive\nevent: x\ndata: 1\ndata: 2\n\n"
    assert await collect(iter_sse_data(_lines(text))) == ["1\n2"]


async def test_iter_sse_data_handles_crlf_and_missing_trailing_blank():
    text = "data: a\r\n\r\ndata: b"
    assert await collect(iter_sse_data(_lines(text))) == ["a", "b"]


# ---------------------------------------------------------------- LlamaClient


@pytest.fixture
async def client():
    async with LlamaClient(BASE_URL, timeout=5) as c:
        yield c


async def chat_all(client: LlamaClient, **kwargs) -> list[ChatChunk]:
    return [c async for c in client.chat([Message("user", "hi")], "test-model", **kwargs)]


@respx.mock(base_url=BASE_URL)
async def test_list_models(respx_mock, client):
    respx_mock.get("/v1/models").respond(
        json={"object": "list", "data": [{"id": "model-a"}, {"id": "model-b"}]}
    )
    assert await client.list_models() == ["model-a", "model-b"]


@respx.mock(base_url=BASE_URL)
async def test_health_ok_and_down(respx_mock, client):
    route = respx_mock.get("/health").respond(json={"status": "ok"})
    assert await client.health() is True
    route.respond(503, json={"status": "loading"})
    assert await client.health() is False
    route.side_effect = httpx.ConnectError("refused")
    assert await client.health() is False


@respx.mock(base_url=BASE_URL)
async def test_chat_stream_with_reasoning_content(respx_mock, client):
    events = [
        delta_event(reasoning="まず"),
        delta_event(reasoning="考える"),
        delta_event(content="答え"),
        delta_event(content="です", finish_reason="stop"),
        delta_event(usage={"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}),
        "[DONE]",
    ]
    respx_mock.post("/v1/chat/completions").mock(return_value=sse_response(events))

    chunks = await chat_all(client)

    assert "".join(c.reasoning for c in chunks) == "まず考える"
    assert "".join(c.content for c in chunks) == "答えです"
    assert [c.finish_reason for c in chunks if c.finish_reason] == ["stop"]
    usage = [c.usage for c in chunks if c.usage][0]
    assert usage.completion_tokens == 4 and usage.total_tokens == 14


@respx.mock(base_url=BASE_URL)
async def test_chat_stream_think_tag_fallback(respx_mock, client):
    # reasoning_content が無く、本文に <think> が混ざって届くケース
    events = [
        delta_event(content="<thi"),
        delta_event(content="nk>推論"),
        delta_event(content="</think>本"),
        delta_event(content="文", finish_reason="stop"),
        "[DONE]",
    ]
    respx_mock.post("/v1/chat/completions").mock(return_value=sse_response(events))

    chunks = await chat_all(client)

    assert "".join(c.reasoning for c in chunks) == "推論"
    assert "".join(c.content for c in chunks) == "本文"


@respx.mock(base_url=BASE_URL)
async def test_chat_request_payload_default(respx_mock, client):
    route = respx_mock.post("/v1/chat/completions").mock(
        return_value=sse_response([delta_event(content="ok"), "[DONE]"])
    )
    await chat_all(client, temperature=0.7)

    body = json.loads(route.calls.last.request.content)
    assert body["model"] == "test-model"
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert body["stream"] is True
    assert body["temperature"] == 0.7
    assert "chat_template_kwargs" not in body


@respx.mock(base_url=BASE_URL)
async def test_chat_disable_thinking_sends_chat_template_kwargs(respx_mock, client):
    route = respx_mock.post("/v1/chat/completions").mock(
        return_value=sse_response([delta_event(content="ok"), "[DONE]"])
    )
    await chat_all(client, enable_thinking=False)

    body = json.loads(route.calls.last.request.content)
    assert body["chat_template_kwargs"] == {"enable_thinking": False}


@respx.mock(base_url=BASE_URL)
async def test_chat_non_stream(respx_mock, client):
    respx_mock.post("/v1/chat/completions").respond(
        json={
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "答え", "reasoning_content": "思考"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }
    )
    chunks = await chat_all(client, stream=False)
    assert len(chunks) == 1
    assert chunks[0].content == "答え"
    assert chunks[0].reasoning == "思考"
    assert chunks[0].usage.total_tokens == 5


@respx.mock(base_url=BASE_URL)
async def test_chat_http_error_becomes_client_error(respx_mock, client):
    respx_mock.post("/v1/chat/completions").respond(
        500, text='{"error":{"message":"model failed to load"}}'
    )
    with pytest.raises(LlamaClientError) as exc_info:
        await chat_all(client)
    assert exc_info.value.status_code == 500
    assert "500" in str(exc_info.value)
    assert "model failed to load" in str(exc_info.value)


@respx.mock(base_url=BASE_URL)
async def test_chat_connect_error_becomes_client_error(respx_mock, client):
    respx_mock.post("/v1/chat/completions").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(LlamaClientError) as exc_info:
        await chat_all(client)
    assert exc_info.value.status_code is None
    assert "接続できません" in str(exc_info.value)


@respx.mock(base_url=BASE_URL)
async def test_chat_error_event_inside_stream(respx_mock, client):
    events = [{"error": {"message": "context size exceeded", "code": 400}}]
    respx_mock.post("/v1/chat/completions").mock(return_value=sse_response(events))
    with pytest.raises(LlamaClientError, match="context size exceeded"):
        await chat_all(client)


@respx.mock(base_url=BASE_URL)
async def test_list_models_404(respx_mock, client):
    respx_mock.get("/v1/models").respond(404, text="not found")
    with pytest.raises(LlamaClientError, match="404"):
        await client.list_models()
