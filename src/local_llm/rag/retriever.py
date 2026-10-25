"""クエリに近いチャンクを探す。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from local_llm.rag.embedder import Embedder
from local_llm.rag.store import RagStore

DEFAULT_TOP_K = 5
MAX_TOP_K = 20


@dataclass(frozen=True)
class RetrievedChunk:
    """検索結果 1 件。n は 1 始まりの引用番号([n] で本文から参照する)。"""

    n: int
    chunk_id: int
    path: str
    title: str
    heading: str
    text: str
    score: float

    def to_source(self) -> dict[str, Any]:
        """SSE の sources イベントや API 応答に載せる形(本文は含めない)。"""
        return {"n": self.n, "path": self.path, "title": self.title, "heading": self.heading, "score": self.score}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Retriever:
    """埋め込みのコサイン類似度(正規化済みなので内積)で上位 k 件を返す。

    インデックスは数百〜数千チャンク規模を想定し、全件を numpy で総当たりする。
    """

    def __init__(self, store: RagStore, embedder: Embedder) -> None:
        self.store = store
        self.embedder = embedder

    async def search(self, query: str, top_k: int = DEFAULT_TOP_K) -> list[RetrievedChunk]:
        top_k = max(1, min(int(top_k), MAX_TOP_K))
        ids, matrix = self.store.load_embeddings(self.embedder.model)
        if not ids:
            return []
        q = await self.embedder.embed_query(query)
        if q.shape[0] != matrix.shape[1]:
            raise ValueError(
                f"クエリの次元 {q.shape[0]} とインデックスの次元 {matrix.shape[1]} が一致しません"
                "(別の埋め込みモデルで索引を作り直してください)"
            )
        scores = matrix @ q
        order = np.argsort(-scores)[:top_k]
        chosen = [ids[int(i)] for i in order]
        chunks = self.store.get_chunks(chosen)
        by_id = {c.id: c for c in chunks}
        results: list[RetrievedChunk] = []
        for i in order:
            c = by_id.get(ids[int(i)])
            if c is None:
                continue
            results.append(
                RetrievedChunk(
                    n=len(results) + 1,
                    chunk_id=c.id,
                    path=c.path,
                    title=c.title,
                    heading=c.heading,
                    text=c.text,
                    score=round(float(scores[int(i)]), 4),
                )
            )
        return results
