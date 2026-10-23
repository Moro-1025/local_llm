"""`/api/*` のエンドポイント。

llama-server への HTTP は Phase 1 の LlamaClient に任せ、ここでは直接書かない。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from local_llm.client import LlamaClient, LlamaClientError
from local_llm.web.db import Database
from local_llm.web.service import ChatService

router = APIRouter(prefix="/api")


# ------------------------------------------------------------------ リクエスト型


class CreateConversationRequest(BaseModel):
    model: str | None = None


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1)
    enable_thinking: bool = True
    # UI のドロップダウンで切り替えたときに会話のモデルを更新する(省略可)
    model: str | None = None


# ------------------------------------------------------------------ 依存の取り出し


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_client(request: Request) -> LlamaClient:
    return request.app.state.client


# ------------------------------------------------------------------ llama-server 中継


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    client = get_client(request)
    ok = await client.health()
    return {"ok": ok, "llama_base_url": client.base_url}


@router.get("/models")
async def models(request: Request) -> dict[str, Any]:
    client = get_client(request)
    try:
        ids = await client.list_models()
    except LlamaClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"models": ids, "default": request.app.state.settings.default_model}


# ------------------------------------------------------------------ 会話


@router.get("/conversations")
def list_conversations(request: Request) -> dict[str, Any]:
    return {"conversations": get_db(request).list_conversations()}


@router.post("/conversations", status_code=201)
def create_conversation(request: Request, body: CreateConversationRequest) -> dict[str, Any]:
    model = body.model or request.app.state.settings.default_model
    return get_db(request).create_conversation(model)


@router.get("/conversations/{conv_id}")
def get_conversation(request: Request, conv_id: int) -> dict[str, Any]:
    conv = get_db(request).get_conversation(conv_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会話が見つかりません")
    return conv


@router.delete("/conversations/{conv_id}", status_code=204)
def delete_conversation(request: Request, conv_id: int) -> None:
    if not get_db(request).delete_conversation(conv_id):
        raise HTTPException(status_code=404, detail="会話が見つかりません")


# ------------------------------------------------------------------ メッセージ送信(SSE)


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/conversations/{conv_id}/messages")
async def send_message(request: Request, conv_id: int, body: SendMessageRequest) -> StreamingResponse:
    db = get_db(request)
    if db.get_conversation(conv_id) is None:
        raise HTTPException(status_code=404, detail="会話が見つかりません")

    service = ChatService(db, get_client(request))

    async def event_stream() -> AsyncIterator[str]:
        async for event in service.stream_reply(
            conv_id, body.content, enable_thinking=body.enable_thinking, model=body.model
        ):
            yield _sse(event)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
