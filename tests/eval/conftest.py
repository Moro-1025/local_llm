"""Phase 6 評価ハーネスのテスト共通部。llama-server(ルーターモード)を respx でモックする。

- GET /models, POST /models/load, /models/unload : 読み込み状態を dict で持つ偽ルーター
- POST /v1/chat/completions(非ストリーム)      : 回答モデルなら質問に応じた本文、採点モデルなら JSON の点数
- POST /v1/embeddings                            : tests/rag/conftest.py の決定的な疑似ベクトル
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rag.conftest import embeddings_side_effect, fake_vector  # noqa: E402

from local_llm.rag.chunker import Chunk  # noqa: E402
from local_llm.rag.embedder import normalize  # noqa: E402
from local_llm.rag.store import RagStore  # noqa: E402

BASE_URL = "http://llama.test"
ANSWER_MODEL = "answer-model"
GRADER_MODEL = "grader-model"
EMBED_MODEL = "embed-test"

# 3 問。1 問目は回答モデルが正解語をすべて言う、2 問目は外す、3 問目は半分
DATASET_ROWS = [
    {"question": "テストを GPU なしで実行するコマンドは?", "answer": "uv run pytest を実行します。respx でモックします。", "source": "README.md#テスト"},
    {"question": "ベクトル DB は何を使う?", "answer": "SQLite に float32 の BLOB で保存し numpy で総当たりする。", "source": "docs/internal-spec.md#RAG"},
    {"question": "max_steps とは?", "answer": "max_steps はエージェントの最大ステップ数。超えたら budget_exceeded で停止する。", "source": "guide/budget.md#max_steps"},
]

ANSWERS = {
    DATASET_ROWS[0]["question"]: "GPU なしなら uv run pytest を実行します。llama-server は respx でモックします。",
    DATASET_ROWS[1]["question"]: "Pinecone を使います。",
    DATASET_ROWS[2]["question"]: "max_steps はエージェントの最大ステップ数です [1]。",
}


@pytest.fixture
def dataset_path(tmp_path: Path) -> Path:
    p = tmp_path / "qa-v1.jsonl"
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in DATASET_ROWS), encoding="utf-8")
    return p


@pytest.fixture
def rag_db(tmp_path: Path) -> Path:
    """fake_vector と同じ規則で 2 文書を索引済みにした DB(パスにはコーパスの接頭辞が付く)。"""
    db = tmp_path / "chat.db"
    store = RagStore(db)
    store.init_schema()
    docs = {
        "ai_harness/guide/budget.md": [Chunk(0, "max_steps", "max_steps はエージェントの最大ステップ数")],
        "local_llm/README.md": [Chunk(0, "テスト", "uv run pytest を実行する respx でモック")],
    }
    for path, chunks in docs.items():
        emb = normalize(np.array([fake_vector(c.text) for c in chunks], dtype=np.float32))
        store.replace_document(path=path, title=path, sha256=path, chunks=chunks, embeddings=emb, embedding_model=EMBED_MODEL)
    return db


class FakeRouter:
    """ルーターモードの最小モック。load/unload の呼び出し順を記録する。"""

    def __init__(self, models: list[str]) -> None:
        self.status = {m: "unloaded" for m in models}
        self.calls: list[tuple[str, str]] = []
        self.chat_requests: list[dict] = []

    def models(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": m, "status": {"value": s}} for m, s in self.status.items()]})

    def load(self, request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        if model not in self.status:
            return httpx.Response(400, json={"error": {"message": "unknown model"}})
        self.status[model] = "loaded"
        self.calls.append(("load", model))
        return httpx.Response(200, json={"success": True})

    def unload(self, request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        self.status[model] = "unloaded"
        self.calls.append(("unload", model))
        return httpx.Response(200, json={"success": True})

    def chat(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.chat_requests.append(body)
        model = body["model"]
        if self.status.get(model) != "loaded":
            return httpx.Response(503, json={"error": {"message": f"{model} is not loaded"}})
        if model == GRADER_MODEL:
            user = body["messages"][-1]["content"]
            score = 5 if "uv run pytest" in user.split("## 受験者の回答")[-1] else 2
            content = json.dumps({"score": score, "reason": "テスト採点"}, ensure_ascii=False)
        else:
            question = body["messages"][-1]["content"]
            content = ANSWERS.get(question, "わかりません")
        return httpx.Response(
            200,
            json={
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
            },
        )


@pytest.fixture
def router() -> FakeRouter:
    return FakeRouter([ANSWER_MODEL, GRADER_MODEL, EMBED_MODEL])


def mount_router(respx_mock, router: FakeRouter) -> None:
    respx_mock.get("/models").mock(side_effect=router.models)
    respx_mock.post("/models/load").mock(side_effect=router.load)
    respx_mock.post("/models/unload").mock(side_effect=router.unload)
    respx_mock.post("/v1/chat/completions").mock(side_effect=router.chat)
    respx_mock.post("/v1/embeddings").mock(side_effect=embeddings_side_effect)
