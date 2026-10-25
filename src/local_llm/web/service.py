"""チャットのオーケストレーション(DB ↔ LlamaClient)。

HTTP の都合(FastAPI、SSE の文字列化)は routes_chat.py に任せ、ここでは
「履歴を組み立てて LLM に投げ、結果を保存する」流れだけを持つ。
Phase 3 の RAG: use_rag=True のときは Retriever で資料を探し、build_prompt() が
system メッセージとして差し込む。検索結果は本文より先に "sources" イベントで返す。
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

from local_llm.client import LlamaClient, LlamaClientError
from local_llm.rag.prompt import build_rag_messages
from local_llm.rag.retriever import DEFAULT_TOP_K, RetrievedChunk, Retriever
from local_llm.types import Message, Usage
from local_llm.web.db import Database


def build_prompt(
    history: list[dict[str, Any]], retrieved: list[RetrievedChunk] | None = None
) -> list[Message]:
    """DB のメッセージ列から llama-server に送る messages を作る。

    reasoning は送らない(Qwen3 の推奨どおり、過去の thinking はコンテキストに含めない)。
    retrieved があれば、先頭に RAG の system メッセージ(指示 + 資料)を差し込む。
    """
    messages = [Message(m["role"], m["content"]) for m in history if m["content"]]
    if retrieved:
        return build_rag_messages(messages, retrieved)
    return messages


class ChatService:
    """1 会話に対する「送信 → ストリーミング応答 → 保存」を担う。"""

    def __init__(self, db: Database, client: LlamaClient, retriever: Retriever | None = None) -> None:
        self.db = db
        self.client = client
        self.retriever = retriever

    async def stream_reply(
        self,
        conv_id: int,
        content: str,
        *,
        enable_thinking: bool = True,
        model: str | None = None,
        use_rag: bool = False,
        top_k: int = DEFAULT_TOP_K,
    ) -> AsyncIterator[dict[str, Any]]:
        """ユーザー発言を保存し、アシスタントの応答をイベント辞書で逐次返す。

        イベントは {"type": "sources"|"reasoning"|"content"|"done"|"error", ...}。
        - ユーザー発言は受信時に即保存する
        - use_rag=True なら最新のユーザー発言で検索し、本文より先に sources を返す
        - アシスタント発言は完了時に保存する。途中で切断・失敗したら、そこまでの本文を保存する
        """
        conv = self.db.get_conversation(conv_id)
        if conv is None:
            yield {"type": "error", "text": f"会話 {conv_id} が見つかりません"}
            return

        if model and model != conv["model"]:
            self.db.update_conversation(conv_id, model=model)
            conv["model"] = model
        use_model = conv["model"]

        self.db.add_message(conv_id, "user", content)
        history = self.db.list_messages(conv_id)

        retrieved: list[RetrievedChunk] = []
        if use_rag:
            if self.retriever is None:
                yield {"type": "error", "text": "RAG が設定されていません"}
                return
            try:
                retrieved = await self.retriever.search(content, top_k=top_k)
            except (LlamaClientError, ValueError) as exc:
                yield {"type": "error", "text": f"資料の検索に失敗しました: {exc}"}
                return
            yield {"type": "sources", "items": [c.to_source() for c in retrieved]}
        prompt = build_prompt(history, retrieved)

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        usage: Usage | None = None
        saved = False
        started = time.perf_counter()
        first_token_at: float | None = None

        def save_partial() -> None:
            nonlocal saved
            if saved:
                return
            text = "".join(content_parts)
            reasoning = "".join(reasoning_parts) or None
            if text or reasoning:
                self.db.add_message(conv_id, "assistant", text, reasoning)
            saved = True

        try:
            async for chunk in self.client.chat(prompt, use_model, enable_thinking=enable_thinking):
                if chunk.reasoning:
                    if first_token_at is None:
                        first_token_at = time.perf_counter()
                    reasoning_parts.append(chunk.reasoning)
                    yield {"type": "reasoning", "text": chunk.reasoning}
                if chunk.content:
                    if first_token_at is None:
                        first_token_at = time.perf_counter()
                    content_parts.append(chunk.content)
                    yield {"type": "content", "text": chunk.content}
                if chunk.usage:
                    usage = chunk.usage
        except LlamaClientError as exc:
            save_partial()
            yield {"type": "error", "text": str(exc)}
            return
        finally:
            # クライアント切断(GeneratorExit / CancelledError)でもここを通る
            save_partial()

        elapsed = time.perf_counter() - started
        gen_time = elapsed - ((first_token_at - started) if first_token_at else 0.0)
        tps = (usage.completion_tokens / gen_time) if (usage and gen_time > 0) else None
        yield {
            "type": "done",
            "model": use_model,
            "usage": {
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
            }
            if usage
            else None,
            "tokens_per_second": round(tps, 1) if tps is not None else None,
            "elapsed_seconds": round(elapsed, 2),
            "rag": {"used": True, "sources": len(retrieved)} if use_rag else None,
        }
