"""SQLite のスキーマとクエリ。

標準ライブラリ sqlite3 だけを使う。呼び出しごとに接続を開いて閉じるので、
FastAPI のスレッドプールから同時に呼ばれても安全。
マイグレーション機構は作らず、起動時の CREATE TABLE IF NOT EXISTS だけで済ませる。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT,
    model       TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id  INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role             TEXT NOT NULL,
    content          TEXT NOT NULL,
    reasoning        TEXT,
    created_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages(conversation_id, id);
"""

# 会話タイトルに使う先頭文字数
TITLE_LENGTH = 40


def now_iso() -> str:
    # 並び順の判定に使うので、マイクロ秒まで入れて同一秒内でも順序が決まるようにする
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def make_title(text: str) -> str:
    """最初のユーザー発言から会話タイトルを作る(改行を潰して先頭 40 文字)。"""
    one_line = " ".join(text.split())
    return one_line[:TITLE_LENGTH] or "(無題)"


class Database:
    """会話と メッセージの永続化。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    # ------------------------------------------------------------------ 接続

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
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
        """テーブルを作る(存在すれば何もしない)。親ディレクトリも作る。"""
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    # ------------------------------------------------------------------ 会話

    def list_conversations(self) -> list[dict[str, Any]]:
        """会話一覧を更新日時の新しい順で返す。"""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, title, model, created_at, updated_at "
                "FROM conversations ORDER BY updated_at DESC, id DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def create_conversation(self, model: str, title: str | None = None) -> dict[str, Any]:
        ts = now_iso()
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO conversations (title, model, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (title, model, ts, ts),
            )
            conv_id = cur.lastrowid
        return {"id": conv_id, "title": title, "model": model, "created_at": ts, "updated_at": ts}

    def get_conversation(self, conv_id: int) -> dict[str, Any] | None:
        """会話をメッセージ込みで返す。無ければ None。"""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id, title, model, created_at, updated_at FROM conversations WHERE id = ?",
                (conv_id,),
            ).fetchone()
            if row is None:
                return None
            messages = conn.execute(
                "SELECT id, role, content, reasoning, created_at "
                "FROM messages WHERE conversation_id = ? ORDER BY id",
                (conv_id,),
            ).fetchall()
        conv = dict(row)
        conv["messages"] = [dict(m) for m in messages]
        return conv

    def delete_conversation(self, conv_id: int) -> bool:
        """会話を削除する。削除できたら True。メッセージは外部キーの CASCADE で消える。"""
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
            return cur.rowcount > 0

    def update_conversation(
        self, conv_id: int, *, model: str | None = None, title: str | None = None
    ) -> None:
        """モデルやタイトルを更新し、updated_at を進める。"""
        sets = ["updated_at = ?"]
        params: list[Any] = [now_iso()]
        if model is not None:
            sets.append("model = ?")
            params.append(model)
        if title is not None:
            sets.append("title = ?")
            params.append(title)
        params.append(conv_id)
        with self.connect() as conn:
            conn.execute(f"UPDATE conversations SET {', '.join(sets)} WHERE id = ?", params)

    # ------------------------------------------------------------------ メッセージ

    def add_message(
        self, conv_id: int, role: str, content: str, reasoning: str | None = None
    ) -> dict[str, Any]:
        """メッセージを追加し、会話の updated_at を進める。

        会話にまだタイトルが無く role が user なら、本文の先頭からタイトルを付ける。
        """
        ts = now_iso()
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO messages (conversation_id, role, content, reasoning, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (conv_id, role, content, reasoning, ts),
            )
            msg_id = cur.lastrowid
            if role == "user":
                conn.execute(
                    "UPDATE conversations SET title = ? WHERE id = ? AND (title IS NULL OR title = '')",
                    (make_title(content), conv_id),
                )
            conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (ts, conv_id))
        return {
            "id": msg_id,
            "role": role,
            "content": content,
            "reasoning": reasoning,
            "created_at": ts,
        }

    def list_messages(self, conv_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, role, content, reasoning, created_at "
                "FROM messages WHERE conversation_id = ? ORDER BY id",
                (conv_id,),
            ).fetchall()
        return [dict(r) for r in rows]
