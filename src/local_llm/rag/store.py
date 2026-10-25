"""RAG のインデックス(documents / chunks)を SQLite に保存する。

チャット履歴と同じ DB ファイル(既定 data/chat.db)に別テーブルとして置く。
埋め込みは float32 の生バイト列(BLOB)で持ち、検索時に numpy で一括読み出す。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from local_llm.rag.chunker import Chunk

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    path        TEXT    NOT NULL UNIQUE,
    title       TEXT    NOT NULL,
    sha256      TEXT    NOT NULL,
    indexed_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id     INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal         INTEGER NOT NULL,
    heading         TEXT    NOT NULL DEFAULT '',
    text            TEXT    NOT NULL,
    embedding       BLOB    NOT NULL,
    embedding_model TEXT    NOT NULL,
    token_count     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_chunks_model ON chunks(embedding_model);
"""


@dataclass(frozen=True)
class StoredChunk:
    id: int
    path: str
    title: str
    ordinal: int
    heading: str
    text: str


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class RagStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    # ------------------------------------------------------------------ documents

    def list_documents(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT id, path, title, sha256, indexed_at FROM documents ORDER BY path").fetchall()
        return [dict(r) for r in rows]

    def get_document_sha(self, path: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT sha256 FROM documents WHERE path = ?", (path,)).fetchone()
        return row["sha256"] if row else None

    def delete_document(self, path: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM documents WHERE path = ?", (path,))
        return cur.rowcount > 0

    def replace_document(
        self,
        *,
        path: str,
        title: str,
        sha256: str,
        chunks: list[Chunk],
        embeddings: np.ndarray,
        embedding_model: str,
    ) -> int:
        """文書とそのチャンクを丸ごと入れ替え、document_id を返す。"""
        if embeddings.shape[0] != len(chunks):
            raise ValueError(f"チャンク {len(chunks)} 件に対し埋め込みが {embeddings.shape[0]} 件です")
        emb = np.ascontiguousarray(embeddings, dtype=np.float32)
        with self.connect() as conn:
            conn.execute("DELETE FROM documents WHERE path = ?", (path,))
            cur = conn.execute(
                "INSERT INTO documents (path, title, sha256, indexed_at) VALUES (?, ?, ?, ?)",
                (path, title, sha256, now_iso()),
            )
            doc_id = int(cur.lastrowid)
            conn.executemany(
                "INSERT INTO chunks (document_id, ordinal, heading, text, embedding, embedding_model, token_count)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (doc_id, c.ordinal, c.heading, c.text, emb[i].tobytes(), embedding_model, len(c.text))
                    for i, c in enumerate(chunks)
                ],
            )
        return doc_id

    # ------------------------------------------------------------------ chunks

    def load_embeddings(self, embedding_model: str) -> tuple[list[int], np.ndarray]:
        """指定モデルで作られた全チャンクの (id 列, 行列) を返す。無ければ空。"""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, embedding FROM chunks WHERE embedding_model = ? ORDER BY id", (embedding_model,)
            ).fetchall()
        if not rows:
            return [], np.zeros((0, 0), dtype=np.float32)
        ids = [int(r["id"]) for r in rows]
        matrix = np.vstack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
        return ids, matrix

    def get_chunks(self, ids: list[int]) -> list[StoredChunk]:
        """id 列の順序を保ってチャンクを返す。"""
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT c.id, d.path, d.title, c.ordinal, c.heading, c.text"
                f" FROM chunks c JOIN documents d ON d.id = c.document_id WHERE c.id IN ({placeholders})",
                ids,
            ).fetchall()
        by_id = {int(r["id"]): StoredChunk(**dict(r)) for r in rows}
        return [by_id[i] for i in ids if i in by_id]

    def status(self) -> dict[str, Any]:
        """`GET /api/rag/status` 用の集計。"""
        with self.connect() as conn:
            docs = conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"]
            chunks = conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
            models = [
                r["embedding_model"]
                for r in conn.execute("SELECT DISTINCT embedding_model FROM chunks ORDER BY 1").fetchall()
            ]
            last = conn.execute("SELECT MAX(indexed_at) AS t FROM documents").fetchone()["t"]
        return {"documents": int(docs), "chunks": int(chunks), "embedding_models": models, "last_indexed_at": last}
