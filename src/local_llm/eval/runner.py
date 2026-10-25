"""評価の実行: 回答 → 採点の 2 段。

1. 回答モデルをルーターに読み込み(`/models/load`)、各問に temperature 0 / thinking off で答えさせる。
   RAG あり構成は Phase 3 の Retriever で上位 k 件を検索し、prompt.build_rag_messages で差し込む。
2. 回答モデルを外し(`/models/unload`)、採点モデルを読み込んで LLM 採点をする。
   12 GB の VRAM に大型モデルと採点モデルを同時に載せないための順番。

採点モデル == 回答モデルなら実行前に GraderSeparationError で止める(契約 GRADER_SEPARATION)。
途中経過は <out>/<basename>.partial.json に書き、失敗しても回答までは残る。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from local_llm.client import LlamaClient, LlamaClientError
from local_llm.eval.configs import EvalConfig
from local_llm.eval.dataset import Dataset, QaItem, select_items
from local_llm.eval.graders import (
    DEFAULT_THRESHOLD,
    GRADER_RESPONSE_FORMAT,
    build_grader_messages,
    grade_deterministic,
    parse_llm_grade,
    retrieval_hit,
)
from local_llm.eval.report import RESULT_SCHEMA_VERSION, compute_metrics
from local_llm.rag.prompt import build_rag_messages
from local_llm.rag.retriever import Retriever
from local_llm.types import Message

ANSWER_SYSTEM_PROMPT = (
    "あなたは local_llm プロジェクトと ai_harness のドキュメントに詳しいアシスタントです。"
    "日本語で簡潔に答えてください。"
)
DEFAULT_ANSWER_MAX_TOKENS = 400
DEFAULT_GRADER_MAX_TOKENS = 200


class GraderSeparationError(ValueError):
    """採点モデルが回答モデルと同じ(同じモデルが自分を採点してはいけない)。"""


@dataclass
class RunOptions:
    n: int | None = None
    seed: int = 42
    threshold: float = DEFAULT_THRESHOLD
    answer_max_tokens: int = DEFAULT_ANSWER_MAX_TOKENS
    grader_max_tokens: int = DEFAULT_GRADER_MAX_TOKENS
    llm_grade: bool = True
    manage_models: bool = True  # ルーターの load / unload を使う(テストや単体モデルサーバでは False)
    system_prompt: str = ANSWER_SYSTEM_PROMPT
    partial_path: Path | None = None
    log: Callable[[str], None] = field(default=lambda _msg: None)


def ensure_grader_separation(answer_model: str, grader_model: str) -> None:
    if answer_model.strip() == grader_model.strip():
        raise GraderSeparationError(
            f"採点モデルと回答モデルが同じです({answer_model})。--grader-model で別のモデルを指定してください"
        )


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


class EvalRunner:
    def __init__(
        self,
        client: LlamaClient,
        config: EvalConfig,
        *,
        grader_model: str,
        retriever: Retriever | None = None,
        options: RunOptions | None = None,
    ) -> None:
        ensure_grader_separation(config.answer_model, grader_model)
        if config.use_rag and retriever is None:
            raise ValueError(f"構成 {config.id} は RAG ありですが retriever がありません")
        self.client = client
        self.config = config
        self.grader_model = grader_model
        self.retriever = retriever
        self.opt = options or RunOptions()

    # ------------------------------------------------------------ モデルの載せ替え

    async def _load(self, model: str) -> None:
        if not self.opt.manage_models:
            return
        self.opt.log(f"モデルを読み込み中: {model}")
        await self.client.router_load(model)

    async def _unload(self, model: str) -> None:
        if not self.opt.manage_models:
            return
        try:
            await self.client.router_unload(model)
        except LlamaClientError as exc:
            self.opt.log(f"unload に失敗(続行): {exc}")

    # ------------------------------------------------------------ 1 段目: 回答

    async def answer_one(self, item: QaItem) -> dict[str, Any]:
        history = [Message("system", self.opt.system_prompt), Message("user", item.question)]
        retrieval: dict[str, Any] | None = None
        if self.config.use_rag and self.retriever is not None:
            retrieved = await self.retriever.search(item.question, top_k=self.config.top_k)
            paths = [c.path for c in retrieved]
            retrieval = {
                "top_k": self.config.top_k,
                "sources": [c.to_source() for c in retrieved],
                "hit_top3": retrieval_hit(item.source_path, paths, top=3),
                "hit_topk": retrieval_hit(item.source_path, paths),
            }
            history = build_rag_messages(history, retrieved)

        started = time.perf_counter()
        chunk = None
        async for chunk in self.client.chat(
            history,
            self.config.answer_model,
            stream=False,
            temperature=0.0,
            enable_thinking=False,
            max_tokens=self.opt.answer_max_tokens,
        ):
            pass
        sec = time.perf_counter() - started
        response = (chunk.content if chunk else "").strip()
        usage = chunk.usage if chunk else None
        completion_tokens = usage.completion_tokens if usage else 0
        det = grade_deterministic(item.answer, response, threshold=self.opt.threshold)
        return {
            "index": item.index,
            "question": item.question,
            "expected": item.answer,
            "source": item.source,
            "response": response,
            "finish_reason": chunk.finish_reason if chunk else None,
            "retrieval": retrieval,
            "deterministic": det.to_dict(),
            "llm": None,
            "timing": {
                "sec": round(sec, 2),
                "prompt_tokens": usage.prompt_tokens if usage else None,
                "completion_tokens": completion_tokens or None,
                "tok_s": round(completion_tokens / sec, 1) if (sec > 0 and completion_tokens) else None,
            },
        }

    # ------------------------------------------------------------ 2 段目: LLM 採点

    async def grade_one(self, record: dict[str, Any]) -> dict[str, Any]:
        messages = build_grader_messages(record["question"], record["expected"], record["response"])
        chunk = None
        async for chunk in self.client.chat(
            messages,
            self.grader_model,
            stream=False,
            temperature=0.0,
            enable_thinking=False,
            max_tokens=self.opt.grader_max_tokens,
            response_format=GRADER_RESPONSE_FORMAT,
        ):
            pass
        return parse_llm_grade(chunk.content if chunk else "").to_dict()

    # ------------------------------------------------------------ 全体

    async def run(self, dataset: Dataset, *, suite: str = "qa") -> dict[str, Any]:
        items = select_items(dataset, self.opt.n, self.opt.seed)
        started = _now()
        cfg = self.config
        result: dict[str, Any] = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "suite": suite,
            "config": cfg.to_dict(),
            "dataset": {
                "path": dataset.path.as_posix(),
                "name": dataset.name,
                "version": dataset.version,
                "sha256": dataset.sha256,
                "total": dataset.total,
                "n": len(items),
                "seed": self.opt.seed,
                "indices": [it.index for it in items],
            },
            "answer": {
                "model": cfg.answer_model,
                "temperature": 0.0,
                "max_tokens": self.opt.answer_max_tokens,
                "enable_thinking": False,
                "system_prompt": self.opt.system_prompt,
                "rag": {"enabled": cfg.use_rag, "top_k": cfg.top_k if cfg.use_rag else None},
            },
            "grader": {
                "model": self.grader_model,
                "temperature": 0.0,
                "max_tokens": self.opt.grader_max_tokens,
                "enable_thinking": False,
                "scale": {"min": 1, "max": 5},
                "differs_from_answer_model": self.grader_model.strip() != cfg.answer_model.strip(),
                "llm_grading_enabled": self.opt.llm_grade,
            },
            "server": {"base_url": self.client.base_url},
            "started_at": _iso(started),
            "finished_at": None,
            "metrics": {},
            "items": [],
        }

        # 1 段目
        await self._load(cfg.answer_model)
        records: list[dict[str, Any]] = []
        for i, item in enumerate(items, 1):
            rec = await self.answer_one(item)
            records.append(rec)
            d = rec["deterministic"]
            self.opt.log(
                f"[{cfg.id} 回答] {i}/{len(items)} recall={d['keyword_recall']:.2f} "
                f"{'OK' if d['correct'] else 'NG'} ({rec['timing']['sec']}s)"
            )
            self._write_partial(result, records)
        await self._unload(cfg.answer_model)

        # 2 段目
        if self.opt.llm_grade:
            await self._load(self.grader_model)
            for i, rec in enumerate(records, 1):
                rec["llm"] = await self.grade_one(rec)
                self.opt.log(f"[{cfg.id} 採点] {i}/{len(records)} score={rec['llm']['score']}")
                self._write_partial(result, records)
            await self._unload(self.grader_model)

        result["items"] = records
        result["metrics"] = compute_metrics(
            records, threshold=self.opt.threshold, use_rag=cfg.use_rag, top_k=cfg.top_k if cfg.use_rag else None
        )
        result["finished_at"] = _iso(_now())
        return result

    def _write_partial(self, result: dict[str, Any], records: list[dict[str, Any]]) -> None:
        if self.opt.partial_path is None:
            return
        import json

        snapshot = dict(result)
        snapshot["items"] = records
        self.opt.partial_path.parent.mkdir(parents=True, exist_ok=True)
        self.opt.partial_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
