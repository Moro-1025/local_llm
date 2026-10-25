"""Web API(use_rag / sources / status / search)と CLI の /rag(RAG-003)。"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import respx
from fastapi.testclient import TestClient
from rich.console import Console

from local_llm.cli import ChatSession
from local_llm.client import LlamaClient
from local_llm.config import Settings
from local_llm.history import ChatLogger
from local_llm.rag.chunker import Chunk
from local_llm.rag.embedder import Embedder, normalize
from local_llm.rag.retriever import Retriever
from local_llm.rag.store import RagStore
from local_llm.web.app import create_app
from local_llm.web.service import build_prompt

from conftest import BASE_URL, delta_event, sse_response
from rag.conftest import embeddings_side_effect, fake_vector

EMBED_MODEL = "embed-test"


def seed_index(db_path: Path) -> RagStore:
    """fake_vector と同じ規則で 2 文書を索引済みの状態にする。"""
    store = RagStore(db_path)
    store.init_schema()
    docs = {
        "guide/budget.md": [Chunk(0, "実行予算 > max_steps", "max_steps はエージェントの最大ステップ数")],
        "evidence.md": [Chunk(0, "証拠", "自己申告より証拠を優先する")],
    }
    for path, chunks in docs.items():
        emb = normalize(np.array([fake_vector(c.text) for c in chunks], dtype=np.float32))
        store.replace_document(path=path, title=path, sha256=path, chunks=chunks, embeddings=emb, embedding_model=EMBED_MODEL)
    return store


@pytest.fixture
def client(tmp_path: Path):
    settings = Settings(
        base_url=BASE_URL, default_model="model-a", db_path=tmp_path / "chat.db", timeout=5,
        embed_model=EMBED_MODEL, corpus_dir=tmp_path / "corpus",
    )
    seed_index(settings.db_path)
    with TestClient(create_app(settings)) as c:
        yield c


def read_sse(response) -> list[dict]:
    return [json.loads(line[5:].strip()) for line in response.iter_lines() if line.startswith("data:")]


# ---------------------------------------------------------------- status / search


def test_rag_status(client: TestClient):
    res = client.get("/api/rag/status")
    assert res.status_code == 200
    body = res.json()
    assert body["documents"] == 2
    assert body["chunks"] == 2
    assert body["embedding_model"] == EMBED_MODEL
    assert body["embedding_models"] == [EMBED_MODEL]
    assert body["corpus_dir"].endswith("corpus")


@respx.mock(base_url=BASE_URL)
def test_rag_search(respx_mock, client: TestClient):
    respx_mock.post("/v1/embeddings").mock(side_effect=embeddings_side_effect)
    res = client.get("/api/rag/search", params={"q": "max_steps とは", "top_k": 1})
    assert res.status_code == 200
    body = res.json()
    assert body["query"] == "max_steps とは"
    assert len(body["results"]) == 1
    assert body["results"][0]["path"] == "guide/budget.md"
    assert "max_steps" in body["results"][0]["text"]  # search は本文つき

    assert client.get("/api/rag/search", params={"q": ""}).status_code == 422
    assert client.get("/api/rag/search", params={"q": "x", "top_k": 99}).status_code == 422


@respx.mock(base_url=BASE_URL)
def test_rag_search_server_down(respx_mock, client: TestClient):
    respx_mock.post("/v1/embeddings").respond(status_code=503, text="loading")
    res = client.get("/api/rag/search", params={"q": "x"})
    assert res.status_code == 502


# ---------------------------------------------------------------- messages with use_rag


@respx.mock(base_url=BASE_URL)
def test_send_message_with_rag_emits_sources_first(respx_mock, client: TestClient):
    """RAG-003: sources イベントが本文より先に届き、プロンプトに資料が差し込まれる。"""
    respx_mock.post("/v1/embeddings").mock(side_effect=embeddings_side_effect)
    chat = respx_mock.post("/v1/chat/completions").mock(
        return_value=sse_response([delta_event(content="答え [1]", finish_reason="stop"), "[DONE]"])
    )
    conv = client.post("/api/conversations", json={}).json()
    with client.stream(
        "POST", f"/api/conversations/{conv['id']}/messages",
        json={"content": "max_steps とは", "use_rag": True, "top_k": 1},
    ) as res:
        events = read_sse(res)

    assert [e["type"] for e in events] == ["sources", "content", "done"]
    assert events[0]["items"] == [
        {"n": 1, "path": "guide/budget.md", "title": "guide/budget.md", "heading": "実行予算 > max_steps",
         "score": events[0]["items"][0]["score"]},
    ]
    assert events[2]["rag"] == {"used": True, "sources": 1}

    sent = json.loads(chat.calls[0].request.content)
    assert sent["messages"][0]["role"] == "system"
    assert "以下の資料だけを根拠に答え" in sent["messages"][0]["content"]
    assert "[1] guide/budget.md › 実行予算 > max_steps" in sent["messages"][0]["content"]
    assert sent["messages"][-1] == {"role": "user", "content": "max_steps とは"}

    # 保存された会話には system は含まれず、user/assistant だけ
    saved = client.get(f"/api/conversations/{conv['id']}").json()["messages"]
    assert [m["role"] for m in saved] == ["user", "assistant"]
    assert saved[1]["content"] == "答え [1]"


@respx.mock(base_url=BASE_URL)
def test_send_message_without_rag_has_no_sources(respx_mock, client: TestClient):
    respx_mock.post("/v1/chat/completions").mock(
        return_value=sse_response([delta_event(content="hi", finish_reason="stop"), "[DONE]"])
    )
    conv = client.post("/api/conversations", json={}).json()
    with client.stream("POST", f"/api/conversations/{conv['id']}/messages", json={"content": "こんにちは"}) as res:
        events = read_sse(res)
    assert [e["type"] for e in events] == ["content", "done"]
    assert events[1]["rag"] is None
    # /v1/embeddings のルートは登録していない。呼ばれていれば respx が例外にするので、ここまで来れば未使用


@respx.mock(base_url=BASE_URL)
def test_send_message_rag_embedding_failure_is_error_event(respx_mock, client: TestClient):
    respx_mock.post("/v1/embeddings").respond(status_code=500, text="x")
    conv = client.post("/api/conversations", json={}).json()
    with client.stream(
        "POST", f"/api/conversations/{conv['id']}/messages", json={"content": "q", "use_rag": True}
    ) as res:
        events = read_sse(res)
    assert events == [{"type": "error", "text": events[0]["text"]}]
    assert "資料の検索に失敗" in events[0]["text"]


def test_build_prompt_with_and_without_retrieved():
    history = [{"role": "user", "content": "q"}, {"role": "assistant", "content": ""}]
    assert [m.role for m in build_prompt(history)] == ["user"]
    from local_llm.rag.retriever import RetrievedChunk

    rc = RetrievedChunk(n=1, chunk_id=1, path="a.md", title="A", heading="", text="t", score=1.0)
    out = build_prompt(history, [rc])
    assert [m.role for m in out] == ["system", "user"]


def test_top_k_validation(client: TestClient):
    conv = client.post("/api/conversations", json={}).json()
    res = client.post(f"/api/conversations/{conv['id']}/messages", json={"content": "q", "top_k": 0})
    assert res.status_code == 422


# ---------------------------------------------------------------- CLI /rag


def make_session(tmp_path: Path, client: LlamaClient, retriever: Retriever | None) -> tuple[ChatSession, Console]:
    console = Console(record=True, width=120, file=open(tmp_path / "out.txt", "w", encoding="utf-8"))
    session = ChatSession(client, console, ChatLogger(tmp_path), model="model-a", retriever=retriever)
    return session, console


async def test_cli_rag_command_without_retriever(tmp_path: Path):
    async with LlamaClient(BASE_URL) as client:
        session, console = make_session(tmp_path, client, None)
        assert await session.handle_command("/rag on") is True
        assert session.use_rag is False
        assert "設定されていません" in console.export_text()


@respx.mock(base_url=BASE_URL)
async def test_cli_rag_toggle_and_ask(respx_mock, tmp_path: Path):
    respx_mock.post("/v1/embeddings").mock(side_effect=embeddings_side_effect)
    chat = respx_mock.post("/v1/chat/completions").mock(
        return_value=sse_response([delta_event(content="答え [1]", finish_reason="stop"), "[DONE]"])
    )
    store = seed_index(tmp_path / "rag.db")
    async with LlamaClient(BASE_URL) as client:
        retriever = Retriever(store, Embedder(client, EMBED_MODEL))
        session, console = make_session(tmp_path, client, retriever)
        assert session.use_rag is False
        await session.handle_command("/rag")
        assert "rag: off" in console.export_text()
        await session.handle_command("/rag on")
        assert session.use_rag is True
        await session.handle_command("/rag maybe")
        assert "使い方" in console.export_text()

        await session.ask("max_steps とは")
        text = console.export_text()
        assert "[1] guide/budget.md › 実行予算 > max_steps" in text
        assert "答え [1]" in text
        sent = json.loads(chat.calls[0].request.content)
        assert sent["messages"][0]["role"] == "system"
        assert "max_steps はエージェントの最大ステップ数" in sent["messages"][0]["content"]
        # 履歴には system を積まない(次回の検索結果で差し替える)
        assert [m.role for m in session.history] == ["user", "assistant"]

        await session.handle_command("/rag off")
        await session.ask("二問目")
        sent = json.loads(chat.calls[1].request.content)
        assert sent["messages"][0]["role"] == "user"


@respx.mock(base_url=BASE_URL)
async def test_cli_rag_search_failure_aborts_send(respx_mock, tmp_path: Path):
    respx_mock.post("/v1/embeddings").respond(status_code=500, text="x")
    # /v1/chat/completions は登録しない: 呼ばれたら respx が例外にする = 送信が中止された証拠
    store = seed_index(tmp_path / "rag.db")
    async with LlamaClient(BASE_URL) as client:
        session, console = make_session(tmp_path, client, Retriever(store, Embedder(client, EMBED_MODEL)))
        await session.handle_command("/rag on")
        await session.ask("q")
        assert "資料の検索に失敗" in console.export_text()
        assert session.history == []


# ---------------------------------------------------------------- 埋め込みサーバを分ける設定


def test_settings_embed_base_url(monkeypatch):
    from local_llm.config import load_settings

    monkeypatch.delenv("LLAMA_EMBED_BASE_URL", raising=False)
    monkeypatch.setenv("LLAMA_BASE_URL", "http://a:1/")
    s = load_settings()
    assert s.embed_base_url is None and s.effective_embed_base_url == "http://a:1"
    monkeypatch.setenv("LLAMA_EMBED_BASE_URL", "http://b:2/")
    assert load_settings().effective_embed_base_url == "http://b:2"


def test_app_uses_separate_embed_client(tmp_path: Path):
    settings = Settings(
        base_url=BASE_URL, db_path=tmp_path / "chat.db", timeout=5, embed_model=EMBED_MODEL,
        embed_base_url="http://embed.test",
    )
    seed_index(settings.db_path)
    with TestClient(create_app(settings)) as c:
        assert c.app.state.embed_client is not c.app.state.client
        assert c.app.state.embed_client.base_url == "http://embed.test"
        with respx.mock(base_url="http://embed.test") as m:
            m.post("/v1/embeddings").mock(side_effect=embeddings_side_effect)
            res = c.get("/api/rag/search", params={"q": "max_steps とは", "top_k": 1})
        assert res.status_code == 200
        assert res.json()["results"][0]["path"] == "guide/budget.md"
