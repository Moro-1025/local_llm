"""client.embed / store / indexer / retriever / prompt のテスト(RAG-001, RAG-004)。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import respx

from local_llm.client import LlamaClient, LlamaClientError
from local_llm.rag.chunker import Chunk
from local_llm.rag.embedder import Embedder, normalize
from local_llm.rag.indexer import index_corpus
from local_llm.rag.prompt import RAG_SYSTEM_PROMPT, build_rag_messages, format_context
from local_llm.rag.retriever import RetrievedChunk, Retriever
from local_llm.rag.store import RagStore
from local_llm.types import Message

from conftest import BASE_URL
from rag.conftest import DIM, embeddings_side_effect, fake_vector

EMBED_MODEL = "embed-test"


# ---------------------------------------------------------------- client.embed


@respx.mock(base_url=BASE_URL)
async def test_client_embed_orders_by_index(respx_mock):
    route = respx_mock.post("/v1/embeddings").respond(
        json={"data": [{"index": 1, "embedding": [0.0, 1.0]}, {"index": 0, "embedding": [1.0, 0.0]}]}
    )
    async with LlamaClient(BASE_URL) as client:
        vecs = await client.embed(["a", "b"], "m")
        assert vecs == [[1.0, 0.0], [0.0, 1.0]]
        assert await client.embed([], "m") == []  # 空入力は API を呼ばない
    assert route.call_count == 1
    import json

    assert json.loads(route.calls[0].request.content) == {"model": "m", "input": ["a", "b"]}


@respx.mock(base_url=BASE_URL)
async def test_client_embed_errors(respx_mock):
    respx_mock.post("/v1/embeddings").respond(json={"data": [{"index": 0, "embedding": [1.0]}]})
    async with LlamaClient(BASE_URL) as client:
        with pytest.raises(LlamaClientError, match="件数"):
            await client.embed(["a", "b"], "m")
    respx_mock.post("/v1/embeddings").respond(status_code=500, text="boom")
    async with LlamaClient(BASE_URL) as client:
        with pytest.raises(LlamaClientError, match="HTTP 500"):
            await client.embed(["a"], "m")


# ---------------------------------------------------------------- embedder


@respx.mock(base_url=BASE_URL)
async def test_embedder_batches_and_normalizes(respx_mock):
    route = respx_mock.post("/v1/embeddings").mock(side_effect=embeddings_side_effect)
    async with LlamaClient(BASE_URL) as client:
        emb = Embedder(client, EMBED_MODEL, batch_size=2)
        matrix = await emb.embed_texts(["a b", "c", "d e f", "g", "h"])
    assert matrix.shape == (5, DIM)
    assert matrix.dtype == np.float32
    assert np.allclose(np.linalg.norm(matrix, axis=1), 1.0)
    assert route.call_count == 3  # 2 + 2 + 1


def test_normalize_keeps_zero_rows():
    out = normalize(np.array([[0.0, 0.0], [3.0, 4.0]]))
    assert out.tolist() == [[0.0, 0.0], [0.6, 0.8]]


# ---------------------------------------------------------------- store


def test_store_replace_and_cascade(tmp_path: Path):
    store = RagStore(tmp_path / "sub" / "rag.db")
    store.init_schema()
    store.init_schema()
    chunks = [Chunk(0, "h0", "text0"), Chunk(1, "h1", "text1")]
    emb = normalize(np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32))
    store.replace_document(path="a.md", title="A", sha256="x" * 64, chunks=chunks, embeddings=emb, embedding_model="m")
    assert store.status() == {
        "documents": 1,
        "chunks": 2,
        "embedding_models": ["m"],
        "last_indexed_at": store.status()["last_indexed_at"],
    }
    assert store.get_document_sha("a.md") == "x" * 64
    # 入れ替えると古いチャンクは消える(ON DELETE CASCADE)
    store.replace_document(path="a.md", title="A", sha256="y" * 64, chunks=chunks[:1], embeddings=emb[:1], embedding_model="m")
    ids, matrix = store.load_embeddings("m")
    assert len(ids) == 1 and matrix.shape == (1, 2)
    assert store.get_chunks(ids)[0].text == "text0"
    other_ids, other_matrix = store.load_embeddings("other")
    assert other_ids == [] and other_matrix.size == 0
    assert store.delete_document("a.md") is True
    assert store.status()["chunks"] == 0
    with pytest.raises(ValueError):
        store.replace_document(path="b.md", title="B", sha256="z", chunks=chunks, embeddings=emb[:1], embedding_model="m")


# ---------------------------------------------------------------- indexer + retriever(統合)


@respx.mock(base_url=BASE_URL)
async def test_index_and_search(respx_mock, corpus: Path, tmp_path: Path):
    """RAG-001: コーパスを索引し、質問に関連するチャンクが上位に来る。"""
    route = respx_mock.post("/v1/embeddings").mock(side_effect=embeddings_side_effect)
    store = RagStore(tmp_path / "rag.db")
    async with LlamaClient(BASE_URL) as client:
        embedder = Embedder(client, EMBED_MODEL)
        result = await index_corpus(store, embedder, corpus)
        assert sorted(result.indexed) == ["evidence.md", "guide/budget.md"]
        assert result.chunks >= 3
        calls_after_first = route.call_count

        # 2 回目は sha256 が同じなので埋め込みを呼ばない
        result2 = await index_corpus(store, embedder, corpus)
        assert result2.indexed == [] and sorted(result2.skipped) == ["evidence.md", "guide/budget.md"]
        assert route.call_count == calls_after_first

        retriever = Retriever(store, embedder)
        hits = await retriever.search("max_steps とは", top_k=2)
        assert len(hits) == 2
        assert hits[0].path == "guide/budget.md"
        assert "max_steps" in hits[0].heading
        assert hits[0].n == 1 and hits[1].n == 2
        assert hits[0].score >= hits[1].score
        assert hits[0].to_source().keys() == {"n", "path", "title", "heading", "score"}

        hits = await retriever.search("自己申告より証拠を優先する", top_k=1)
        assert hits[0].path == "evidence.md"

        # 文書を消すと索引からも消える
        (corpus / "evidence.md").unlink()
        result3 = await index_corpus(store, embedder, corpus)
        assert result3.deleted == ["evidence.md"]
        assert store.status()["documents"] == 1


@respx.mock(base_url=BASE_URL)
async def test_search_empty_index_and_dimension_mismatch(respx_mock, tmp_path: Path):
    respx_mock.post("/v1/embeddings").mock(side_effect=embeddings_side_effect)
    store = RagStore(tmp_path / "rag.db")
    store.init_schema()
    async with LlamaClient(BASE_URL) as client:
        retriever = Retriever(store, Embedder(client, EMBED_MODEL))
        assert await retriever.search("anything") == []
        # 別次元のベクトルが入っていると分かりやすいエラーになる
        store.replace_document(
            path="x.md", title="X", sha256="s", chunks=[Chunk(0, "", "t")],
            embeddings=np.ones((1, 3), dtype=np.float32), embedding_model=EMBED_MODEL,
        )
        with pytest.raises(ValueError, match="次元"):
            await retriever.search("q")


# ---------------------------------------------------------------- prompt


def _chunk(n: int, path: str, heading: str, text: str) -> RetrievedChunk:
    return RetrievedChunk(n=n, chunk_id=n, path=path, title="T", heading=heading, text=text, score=0.9)


def test_build_rag_messages_prepends_system():
    history = [Message("user", "質問")]
    retrieved = [_chunk(1, "a.md", "節", "本文A"), _chunk(2, "b.md", "", "本文B")]
    out = build_rag_messages(history, retrieved)
    assert out[0].role == "system"
    assert RAG_SYSTEM_PROMPT in out[0].content
    assert "[1] a.md › 節\n本文A" in out[0].content
    assert "[2] b.md\n本文B" in out[0].content
    assert out[1:] == history
    assert build_rag_messages(history, []) == history


def test_build_rag_messages_merges_existing_system():
    history = [Message("system", "日本語で"), Message("user", "質問")]
    out = build_rag_messages(history, [_chunk(1, "a.md", "", "本文")])
    assert len(out) == 2
    assert out[0].content.startswith("日本語で")
    assert "[1] a.md" in out[0].content
    assert format_context([]) == ""


def test_fake_vector_is_deterministic():
    assert fake_vector("a b") == fake_vector("a b")
