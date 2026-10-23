"""CLI のセッションロジック(スラッシュコマンド・履歴)のテスト。入力ループは回さない。"""

from __future__ import annotations

from pathlib import Path

import respx
from rich.console import Console

from local_llm.cli import ChatSession, parse_args
from local_llm.client import LlamaClient
from local_llm.history import ChatLogger, read_log
from local_llm.types import Message

from conftest import BASE_URL, delta_event, sse_response


def make_session(tmp_path: Path, client: LlamaClient) -> tuple[ChatSession, Console]:
    console = Console(record=True, width=120, file=open(tmp_path / "out.txt", "w", encoding="utf-8"))
    session = ChatSession(client, console, ChatLogger(tmp_path), model="model-a")
    return session, console


async def test_commands_change_state(tmp_path: Path):
    async with LlamaClient(BASE_URL) as client:
        session, _ = make_session(tmp_path, client)
        assert await session.handle_command("/model model-b") is True
        assert session.model == "model-b"
        assert await session.handle_command("/think off") is True
        assert session.enable_thinking is False
        assert await session.handle_command("/system 日本語で答えて") is True
        assert session.build_messages()[0] == Message("system", "日本語で答えて")
        session.history.append(Message("user", "x"))
        assert await session.handle_command("/clear") is True
        assert session.history == []
        assert await session.handle_command("/quit") is False


@respx.mock(base_url=BASE_URL)
async def test_model_list_command(respx_mock, tmp_path: Path):
    respx_mock.get("/v1/models").respond(json={"data": [{"id": "model-a"}, {"id": "model-b"}]})
    async with LlamaClient(BASE_URL) as client:
        session, console = make_session(tmp_path, client)
        await session.handle_command("/model")
    text = console.export_text()
    assert "* model-a" in text
    assert "model-b" in text


@respx.mock(base_url=BASE_URL)
async def test_ask_streams_and_logs(respx_mock, tmp_path: Path):
    events = [
        delta_event(reasoning="考える"),
        delta_event(content="答え", finish_reason="stop"),
        delta_event(usage={"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6}),
        "[DONE]",
    ]
    respx_mock.post("/v1/chat/completions").mock(return_value=sse_response(events))

    async with LlamaClient(BASE_URL) as client:
        session, console = make_session(tmp_path, client)
        await session.ask("質問")

    assert session.history == [Message("user", "質問"), Message("assistant", "答え")]
    text = console.export_text()
    assert "考える" in text and "答え" in text
    assert "tok/s" in text

    records = read_log(session.logger.path)
    assert [r["role"] for r in records] == ["user", "assistant"]
    assert records[1]["reasoning"] == "考える"


@respx.mock(base_url=BASE_URL)
async def test_ask_error_removes_user_message(respx_mock, tmp_path: Path):
    respx_mock.post("/v1/chat/completions").respond(503, text="loading")
    async with LlamaClient(BASE_URL) as client:
        session, console = make_session(tmp_path, client)
        await session.ask("質問")
    assert session.history == []
    assert "503" in console.export_text()


def test_parse_args_defaults(monkeypatch):
    monkeypatch.setenv("LLM_DEFAULT_MODEL", "env-model")
    monkeypatch.setenv("LLAMA_BASE_URL", "http://example.test:1234/")
    args = parse_args(["--no-think"])
    assert args.model == "env-model"
    assert args.base_url == "http://example.test:1234"
    assert args.no_think is True


@respx.mock(base_url=BASE_URL)
async def test_check_server_warns_when_model_missing(respx_mock, tmp_path: Path):
    respx_mock.get("/health").respond(json={"status": "ok"})
    respx_mock.get("/v1/models").respond(json={"data": [{"id": "model-x"}]})
    async with LlamaClient(BASE_URL) as client:
        session, console = make_session(tmp_path, client)
        await session._check_server()
    text = console.export_text()
    assert "model-a" in text and "model-x" in text
    assert session.model == "model-a"  # 勝手には切り替えない
