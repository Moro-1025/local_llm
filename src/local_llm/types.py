"""クライアントとアプリで共有するデータ型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

Role = str  # "system" | "user" | "assistant"


@dataclass(frozen=True)
class Message:
    """チャットの 1 メッセージ。OpenAI 互換 API の messages 要素に対応する。"""

    role: Role
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True)
class Usage:
    """トークン使用量。ストリーミングでは最終チャンクにだけ付く。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Usage:
        return cls(
            prompt_tokens=int(data.get("prompt_tokens", 0)),
            completion_tokens=int(data.get("completion_tokens", 0)),
            total_tokens=int(data.get("total_tokens", 0)),
        )


@dataclass(frozen=True)
class ChatChunk:
    """ストリーミング応答の 1 断片。

    content と reasoning は空文字のこともある(usage だけ載った最終チャンクなど)。
    """

    content: str = ""
    reasoning: str = ""
    finish_reason: str | None = None
    usage: Usage | None = None

    @property
    def is_empty(self) -> bool:
        return not (self.content or self.reasoning or self.finish_reason or self.usage)
