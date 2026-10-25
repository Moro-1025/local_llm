"""検索結果をプロンプトに差し込む。"""

from __future__ import annotations

from local_llm.rag.retriever import RetrievedChunk
from local_llm.types import Message

RAG_SYSTEM_PROMPT = (
    "あなたは資料に基づいて回答するアシスタントです。"
    "以下の資料だけを根拠に答え、使った資料を本文中で [n] の形で示してください。"
    "資料に書かれていないことは「資料にはありません」と答え、推測で補わないでください。"
)


def format_context(chunks: list[RetrievedChunk]) -> str:
    """チャンク列を `[n] path (heading)` + 本文の形で連結する。"""
    parts: list[str] = []
    for c in chunks:
        where = f"{c.path}" + (f" › {c.heading}" if c.heading else "")
        parts.append(f"[{c.n}] {where}\n{c.text}")
    return "\n\n".join(parts)


def build_rag_messages(history: list[Message], retrieved: list[RetrievedChunk]) -> list[Message]:
    """履歴の先頭に RAG 用の system メッセージ(指示 + 資料)を置く。

    既存の system メッセージがあれば、その直後に資料を足す(指示の二重化を避ける)。
    検索結果が空なら履歴をそのまま返す。
    """
    if not retrieved:
        return list(history)
    context = "## 資料\n\n" + format_context(retrieved)
    if history and history[0].role == "system":
        merged = Message("system", f"{history[0].content}\n\n{RAG_SYSTEM_PROMPT}\n\n{context}")
        return [merged, *history[1:]]
    return [Message("system", f"{RAG_SYSTEM_PROMPT}\n\n{context}"), *history]
