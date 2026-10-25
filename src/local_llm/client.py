"""llama-server の OpenAI 互換 API を叩く非同期クライアント。

openai SDK は使わず、httpx で `/v1/chat/completions` を呼び、
SSE(Server-Sent Events)を自前でパースする。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from local_llm.types import ChatChunk, Message, Usage

# エラーメッセージに含めるレスポンス本文の最大文字数
_BODY_PREVIEW = 200


class LlamaClientError(Exception):
    """llama-server との通信に失敗したときの例外。

    接続失敗・タイムアウト・HTTP 4xx/5xx をすべてこれにまとめる。
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ThinkTagSplitter:
    """本文に混ざった `<think>...</think>` を reasoning と content に振り分ける。

    ストリーミングではタグがチャンク境界で分断されうるので、
    タグの途中かもしれない末尾を次のチャンクまで保留する。
    """

    OPEN = "<think>"
    CLOSE = "</think>"

    def __init__(self) -> None:
        self._buf = ""
        self._in_think = False

    def feed(self, text: str) -> tuple[str, str]:
        """テキストを追加し、確定した (content, reasoning) を返す。"""
        self._buf += text
        content: list[str] = []
        reasoning: list[str] = []

        while self._buf:
            tag = self.CLOSE if self._in_think else self.OPEN
            idx = self._buf.find(tag)
            if idx != -1:
                piece = self._buf[:idx]
                (reasoning if self._in_think else content).append(piece)
                self._buf = self._buf[idx + len(tag) :]
                self._in_think = not self._in_think
                continue

            # 完全なタグは無いが、末尾がタグの先頭と一致するなら保留する
            keep = 0
            for k in range(min(len(tag) - 1, len(self._buf)), 0, -1):
                if tag.startswith(self._buf[-k:]):
                    keep = k
                    break
            emit, self._buf = self._buf[: len(self._buf) - keep], self._buf[len(self._buf) - keep :]
            (reasoning if self._in_think else content).append(emit)
            break

        return "".join(content), "".join(reasoning)

    def flush(self) -> tuple[str, str]:
        """保留中のテキストを吐き出す(ストリーム終端で呼ぶ)。"""
        rest, self._buf = self._buf, ""
        if self._in_think:
            return "", rest
        return rest, ""


async def iter_sse_data(lines: AsyncIterator[str]) -> AsyncIterator[str]:
    """SSE の行ストリームから、イベントごとの data 文字列を取り出す。

    - `data:` 行を集め、空行でイベント終端とみなす
    - 複数の data 行は改行で連結する(SSE 仕様どおり)
    - `:` で始まるコメント行や `event:` などの他フィールドは無視する
    """
    data_lines: list[str] = []
    async for raw in lines:
        line = raw.rstrip("\r")
        if line == "":
            if data_lines:
                yield "\n".join(data_lines)
                data_lines = []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if field == "data":
            data_lines.append(value[1:] if value.startswith(" ") else value)
    if data_lines:
        yield "\n".join(data_lines)


class LlamaClient:
    """llama-server(OpenAI 互換 API)のラッパ。

    `async with LlamaClient(...) as client:` で使うか、使い終わったら `aclose()` を呼ぶ。
    """

    def __init__(self, base_url: str = "http://127.0.0.1:8080", timeout: float = 600.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)

    async def __aenter__(self) -> LlamaClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ 内部

    @staticmethod
    def _raise_for_status(response: httpx.Response, body: str) -> None:
        if response.status_code >= 400:
            preview = body[:_BODY_PREVIEW].replace("\n", " ")
            raise LlamaClientError(
                f"llama-server が HTTP {response.status_code} を返しました: {preview}",
                status_code=response.status_code,
            )

    @staticmethod
    def _wrap_transport_error(exc: httpx.HTTPError) -> LlamaClientError:
        return LlamaClientError(f"llama-server に接続できません({type(exc).__name__}: {exc})")

    async def _get_json(self, path: str) -> Any:
        try:
            response = await self._http.get(path)
        except httpx.HTTPError as exc:
            raise self._wrap_transport_error(exc) from exc
        self._raise_for_status(response, response.text)
        return response.json()

    # ------------------------------------------------------------------ API

    async def health(self) -> bool:
        """`GET /health` が ok なら True。到達できない場合も False を返す。"""
        try:
            response = await self._http.get("/health")
        except httpx.HTTPError:
            return False
        if response.status_code != 200:
            return False
        try:
            return response.json().get("status") == "ok"
        except (ValueError, AttributeError):
            return False

    async def list_models(self) -> list[str]:
        """`GET /v1/models` からモデル id の一覧を返す。"""
        data = await self._get_json("/v1/models")
        return [m["id"] for m in data.get("data", []) if "id" in m]

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        """`POST /v1/embeddings` でテキスト列をベクトル化する。

        入力と同じ順序・同じ個数のベクトルを返す。llama-server は `data[].index` で
        順序を示すので、それで並べ直す。空リストは API を呼ばずに空リストを返す。
        """
        if not texts:
            return []
        payload = {"model": model, "input": texts}
        try:
            response = await self._http.post("/v1/embeddings", json=payload)
        except httpx.HTTPError as exc:
            raise self._wrap_transport_error(exc) from exc
        self._raise_for_status(response, response.text)
        data = response.json()
        items = data.get("data")
        if not isinstance(items, list) or len(items) != len(texts):
            got = len(items) if isinstance(items, list) else "なし"
            raise LlamaClientError(
                f"/v1/embeddings の応答件数が入力と一致しません(入力 {len(texts)} 件、応答 {got} 件)"
            )
        ordered = sorted(items, key=lambda item: item.get("index", 0))
        vectors: list[list[float]] = []
        for item in ordered:
            vec = item.get("embedding")
            if not isinstance(vec, list):
                raise LlamaClientError("/v1/embeddings の応答に embedding がありません")
            vectors.append([float(x) for x in vec])
        return vectors

    def _build_payload(
        self,
        messages: list[Message],
        model: str,
        *,
        stream: bool,
        temperature: float | None,
        enable_thinking: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [m.to_dict() for m in messages],
            "stream": stream,
        }
        if stream:
            # 最終チャンクに usage を載せてもらう
            payload["stream_options"] = {"include_usage": True}
        if temperature is not None:
            payload["temperature"] = temperature
        if not enable_thinking:
            # Qwen3 系の chat template は enable_thinking で思考の有無を切り替えられる
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        return payload

    async def chat(
        self,
        messages: list[Message],
        model: str,
        *,
        stream: bool = True,
        temperature: float | None = None,
        enable_thinking: bool = True,
    ) -> AsyncIterator[ChatChunk]:
        """`POST /v1/chat/completions` を呼び、応答を ChatChunk で逐次返す。

        stream=False のときも 1 個の ChatChunk を返す(呼び出し側の形を揃えるため)。
        """
        payload = self._build_payload(
            messages, model, stream=stream, temperature=temperature, enable_thinking=enable_thinking
        )
        if not stream:
            yield await self._chat_once(payload)
            return

        splitter = ThinkTagSplitter()
        try:
            async with self._http.stream("POST", "/v1/chat/completions", json=payload) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="replace")
                    self._raise_for_status(response, body)

                async for data in iter_sse_data(response.aiter_lines()):
                    if data.strip() == "[DONE]":
                        break
                    chunk = self._parse_stream_event(data, splitter)
                    if chunk is not None and not chunk.is_empty:
                        yield chunk
        except httpx.HTTPError as exc:
            raise self._wrap_transport_error(exc) from exc

        content, reasoning = splitter.flush()
        if content or reasoning:
            yield ChatChunk(content=content, reasoning=reasoning)

    async def _chat_once(self, payload: dict[str, Any]) -> ChatChunk:
        try:
            response = await self._http.post("/v1/chat/completions", json=payload)
        except httpx.HTTPError as exc:
            raise self._wrap_transport_error(exc) from exc
        self._raise_for_status(response, response.text)
        data = response.json()
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        splitter = ThinkTagSplitter()
        content, reasoning = splitter.feed(message.get("content") or "")
        rest_c, rest_r = splitter.flush()
        content += rest_c
        reasoning = (message.get("reasoning_content") or "") + reasoning + rest_r
        usage = Usage.from_dict(data["usage"]) if data.get("usage") else None
        return ChatChunk(
            content=content,
            reasoning=reasoning,
            finish_reason=choice.get("finish_reason"),
            usage=usage,
        )

    @staticmethod
    def _parse_stream_event(data: str, splitter: ThinkTagSplitter) -> ChatChunk | None:
        """SSE の data(JSON)を ChatChunk に変換する。壊れた JSON は読み飛ばす。"""
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            return None

        # llama-server のエラーは SSE 内に {"error": {...}} として届くことがある
        if "error" in event and "choices" not in event:
            err = event["error"]
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            raise LlamaClientError(f"llama-server がエラーを返しました: {msg}")

        choices = event.get("choices") or []
        delta = choices[0].get("delta") or {} if choices else {}
        finish_reason = choices[0].get("finish_reason") if choices else None

        # --reasoning-format 既定では thinking は reasoning_content に分離されて届く
        reasoning = delta.get("reasoning_content") or ""
        # 本文に <think> が混ざるケースへのフォールバック
        content, extra_reasoning = splitter.feed(delta.get("content") or "")
        reasoning += extra_reasoning

        usage = Usage.from_dict(event["usage"]) if event.get("usage") else None
        return ChatChunk(
            content=content,
            reasoning=reasoning,
            finish_reason=finish_reason,
            usage=usage,
        )
