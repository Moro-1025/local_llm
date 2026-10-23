"""Web API のテスト。FastAPI の TestClient で叩き、llama-server は respx でモックする。"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from local_llm.config import Settings
from local_llm.web.app import create_app
from local_llm.web.db import Database, make_title

from conftest import BASE_URL, delta_event, sse_response


@pytest.fixture
def client(tmp_path: Path):
    settings = Settings(base_url=BASE_URL, default_model="model-a", db_path=tmp_path / "chat.db", timeout=5)
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


def read_sse(response) -> list[dict]:
    """TestClient のストリーミング応答から SSE イベントを集める。"""
    events = []
    for line in response.iter_lines():
        if line.startswith("data:"):
            events.append(json.loads(line[5:].strip()))
    return events


# ---------------------------------------------------------------- DB 単体


def test_db_schema_and_crud(tmp_path: Path):
    db = Database(tmp_path / "sub" / "chat.db")
    db.init_schema()
    db.init_schema()  # 2 回呼んでも壊れない

    conv = db.create_conversation("model-a")
    assert conv["title"] is None
    db.add_message(conv["id"], "user", "  最初の  質問\nです  " + "あ" * 60)
    db.add_message(conv["id"], "assistant", "答え", reasoning="思考")

    got = db.get_conversation(conv["id"])
    # 空白・改行は 1 個のスペースに潰し、先頭 40 文字で切る
    assert got["title"] == "最初の 質問 です " + "あ" * 30
    assert len(got["title"]) == 40
    assert [m["role"] for m in got["messages"]] == ["user", "assistant"]
    assert got["messages"][1]["reasoning"] == "思考"

    # 2 件目のユーザー発言でタイトルは変わらない
    db.add_message(conv["id"], "user", "別の質問")
    assert db.get_conversation(conv["id"])["title"] == got["title"]

    assert db.delete_conversation(conv["id"]) is True
    assert db.delete_conversation(conv["id"]) is False
    assert db.get_conversation(conv["id"]) is None
    assert db.list_messages(conv["id"]) == []  # CASCADE で消えている


def test_db_list_is_newest_first(tmp_path: Path):
    db = Database(tmp_path / "chat.db")
    db.init_schema()
    a = db.create_conversation("m")
    b = db.create_conversation("m")
    db.update_conversation(a["id"], title="古い方を更新")
    ids = [c["id"] for c in db.list_conversations()]
    # 更新した a が先に来る(updated_at はマイクロ秒精度)
    assert ids[0] == a["id"] and set(ids) == {a["id"], b["id"]}


# ---------------------------------------------------------------- 中継系


def test_index_and_static(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "<title>local_llm chat</title>" in res.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


@respx.mock(base_url=BASE_URL)
def test_health_proxies_llama(respx_mock, client):
    route = respx_mock.get("/health").respond(json={"status": "ok"})
    assert client.get("/api/health").json()["ok"] is True
    route.side_effect = httpx.ConnectError("refused")
    body = client.get("/api/health").json()
    assert body["ok"] is False
    assert body["llama_base_url"] == BASE_URL


@respx.mock(base_url=BASE_URL)
def test_models_proxies_llama(respx_mock, client):
    respx_mock.get("/v1/models").respond(json={"data": [{"id": "model-a"}, {"id": "model-b"}]})
    body = client.get("/api/models").json()
    assert body == {"models": ["model-a", "model-b"], "default": "model-a"}


@respx.mock(base_url=BASE_URL)
def test_models_when_llama_down_returns_502(respx_mock, client):
    respx_mock.get("/v1/models").mock(side_effect=httpx.ConnectError("refused"))
    res = client.get("/api/models")
    assert res.status_code == 502
    assert "接続できません" in res.json()["detail"]


# ---------------------------------------------------------------- 会話 CRUD


def test_conversation_crud(client):
    assert client.get("/api/conversations").json() == {"conversations": []}

    created = client.post("/api/conversations", json={"model": "model-b"})
    assert created.status_code == 201
    conv = created.json()
    assert conv["model"] == "model-b" and conv["title"] is None

    # model を省略すると既定モデル
    assert client.post("/api/conversations", json={}).json()["model"] == "model-a"

    got = client.get(f"/api/conversations/{conv['id']}")
    assert got.status_code == 200
    assert got.json()["messages"] == []

    assert len(client.get("/api/conversations").json()["conversations"]) == 2

    assert client.delete(f"/api/conversations/{conv['id']}").status_code == 204
    assert client.get(f"/api/conversations/{conv['id']}").status_code == 404
    assert client.delete(f"/api/conversations/{conv['id']}").status_code == 404


# ---------------------------------------------------------------- メッセージ送信(SSE)


@respx.mock(base_url=BASE_URL)
def test_send_message_streams_and_persists(respx_mock, client):
    events = [
        delta_event(reasoning="考える"),
        delta_event(content="こんにちは"),
        delta_event(content="!", finish_reason="stop"),
        delta_event(usage={"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10}),
        "[DONE]",
    ]
    route = respx_mock.post("/v1/chat/completions").mock(return_value=sse_response(events))
    conv_id = client.post("/api/conversations", json={"model": "model-a"}).json()["id"]

    with client.stream(
        "POST",
        f"/api/conversations/{conv_id}/messages",
        json={"content": "やあ", "enable_thinking": True},
    ) as res:
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/event-stream")
        got = read_sse(res)

    types = [e["type"] for e in got]
    assert types == ["reasoning", "content", "content", "done"]
    assert "".join(e["text"] for e in got if e["type"] == "content") == "こんにちは!"
    done = got[-1]
    assert done["usage"]["total_tokens"] == 10
    assert done["model"] == "model-a"
    assert "tokens_per_second" in done and "elapsed_seconds" in done

    # llama-server へ送った内容: 履歴は DB から組み立て、reasoning は含めない
    sent = json.loads(route.calls.last.request.content)
    assert sent["model"] == "model-a"
    assert sent["messages"] == [{"role": "user", "content": "やあ"}]
    assert "chat_template_kwargs" not in sent

    # DB に user / assistant の両方が保存され、タイトルが付いている
    conv = client.get(f"/api/conversations/{conv_id}").json()
    assert conv["title"] == "やあ"
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant"]
    assert conv["messages"][1]["content"] == "こんにちは!"
    assert conv["messages"][1]["reasoning"] == "考える"


@respx.mock(base_url=BASE_URL)
def test_send_message_second_turn_sends_history_without_reasoning(respx_mock, client):
    route = respx_mock.post("/v1/chat/completions").mock(
        return_value=sse_response([delta_event(reasoning="r", content="A"), "[DONE]"])
    )
    conv_id = client.post("/api/conversations", json={"model": "model-a"}).json()["id"]
    for text in ("1 回目", "2 回目"):
        with client.stream(
            "POST", f"/api/conversations/{conv_id}/messages", json={"content": text, "enable_thinking": False}
        ) as res:
            read_sse(res)

    sent = json.loads(route.calls.last.request.content)
    assert sent["messages"] == [
        {"role": "user", "content": "1 回目"},
        {"role": "assistant", "content": "A"},
        {"role": "user", "content": "2 回目"},
    ]
    assert sent["chat_template_kwargs"] == {"enable_thinking": False}


@respx.mock(base_url=BASE_URL)
def test_send_message_with_model_override_updates_conversation(respx_mock, client):
    route = respx_mock.post("/v1/chat/completions").mock(
        return_value=sse_response([delta_event(content="ok"), "[DONE]"])
    )
    conv_id = client.post("/api/conversations", json={"model": "model-a"}).json()["id"]
    with client.stream(
        "POST", f"/api/conversations/{conv_id}/messages", json={"content": "x", "model": "model-b"}
    ) as res:
        read_sse(res)
    assert json.loads(route.calls.last.request.content)["model"] == "model-b"
    assert client.get(f"/api/conversations/{conv_id}").json()["model"] == "model-b"


@respx.mock(base_url=BASE_URL)
def test_send_message_error_event_and_partial_save(respx_mock, client):
    # 途中までストリームした後に llama-server がエラーイベントを返す
    events = [
        delta_event(content="途中まで"),
        {"error": {"message": "context size exceeded"}},
    ]
    respx_mock.post("/v1/chat/completions").mock(return_value=sse_response(events))
    conv_id = client.post("/api/conversations", json={"model": "model-a"}).json()["id"]

    with client.stream("POST", f"/api/conversations/{conv_id}/messages", json={"content": "q"}) as res:
        got = read_sse(res)

    assert [e["type"] for e in got] == ["content", "error"]
    assert "context size exceeded" in got[-1]["text"]
    msgs = client.get(f"/api/conversations/{conv_id}").json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["content"] == "途中まで"


@respx.mock(base_url=BASE_URL)
def test_send_message_when_llama_down(respx_mock, client):
    respx_mock.post("/v1/chat/completions").mock(side_effect=httpx.ConnectError("refused"))
    conv_id = client.post("/api/conversations", json={"model": "model-a"}).json()["id"]
    with client.stream("POST", f"/api/conversations/{conv_id}/messages", json={"content": "q"}) as res:
        got = read_sse(res)
    assert [e["type"] for e in got] == ["error"]
    # ユーザー発言は保存されるが、アシスタントの空メッセージは保存されない
    msgs = client.get(f"/api/conversations/{conv_id}").json()["messages"]
    assert [m["role"] for m in msgs] == ["user"]


def test_send_message_validation(client):
    conv_id = client.post("/api/conversations", json={}).json()["id"]
    assert client.post(f"/api/conversations/{conv_id}/messages", json={"content": ""}).status_code == 422
    assert client.post("/api/conversations/9999/messages", json={"content": "x"}).status_code == 404
