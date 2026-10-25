"""テキスト → ベクトル。llama-server へは LlamaClient.embed() 経由でのみアクセスする。"""

from __future__ import annotations

import numpy as np

from local_llm.client import LlamaClient

DEFAULT_BATCH_SIZE = 8


def normalize(matrix: np.ndarray) -> np.ndarray:
    """各行を L2 正規化する(零ベクトルはそのまま)。"""
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


class Embedder:
    """LlamaClient を使ってバッチで埋め込みを計算する。"""

    def __init__(self, client: LlamaClient, model: str, *, batch_size: int = DEFAULT_BATCH_SIZE) -> None:
        self.client = client
        self.model = model
        self.batch_size = max(1, batch_size)

    async def embed_texts(self, texts: list[str]) -> np.ndarray:
        """shape (len(texts), dim) の float32 行列(L2 正規化済み)を返す。"""
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        rows: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            rows.extend(await self.client.embed(texts[i : i + self.batch_size], self.model))
        return normalize(np.asarray(rows, dtype=np.float32))

    async def embed_query(self, text: str) -> np.ndarray:
        """1 本のクエリを shape (dim,) で返す。"""
        return (await self.embed_texts([text]))[0]
