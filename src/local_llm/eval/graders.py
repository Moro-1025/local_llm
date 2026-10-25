"""採点器。

1 段目(決定論的、GPU 不要):
  - keyword_recall: 正解文のキーワード(英数字 3 字以上 / カタカナ・漢字 2 字以上、一般語を除外)のうち
    応答に含まれる割合。閾値(既定 0.5)以上なら「正答」。Phase 5 の暫定採点(training/eval_quick.py)と同じ規則
    なので、LORA-005 の数値と比べられる
  - cited: 応答に [n] 形式の引用があるか(RAG あり構成で「資料を根拠に答えた」かの目安)
  - retrieval_hit: 検索結果の上位 k 件に、問いの出典ファイル(source のパス部分)が含まれるか

2 段目(LLM 採点): 採点モデルに「質問・正解・応答」を渡し、1〜5 の整数と短い理由を JSON で返させる。
  温度 0、thinking off、JSON Schema で形を強制する。採点モデルは回答モデルと別(契約 GRADER_SEPARATION)。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from local_llm.types import Message

_ASCII = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./\-]{2,}")
_CJK = re.compile(r"[゠-ヿ一-鿿々]{2,}")
_CITATION = re.compile(r"\[\d+\]")
STOP_WORDS = frozenset(
    {
        "こと", "ため", "必要", "場合", "使用", "設定", "実行", "可能", "内容", "指定", "存在", "対応", "確認",
        "方法", "行う", "など", "また", "この", "その", "よう", "される", "ます", "です", "いる", "ある", "する",
    }
)
DEFAULT_THRESHOLD = 0.5
LLM_SCORE_MIN, LLM_SCORE_MAX = 1, 5


def keywords(answer: str) -> list[str]:
    """正解文から採点に使う語を取り出す(重複なし、ソート済み)。"""
    ks = {k.lower() for k in _ASCII.findall(answer)} | {k for k in _CJK.findall(answer) if k not in STOP_WORDS}
    return sorted(ks)


@dataclass(frozen=True)
class DeterministicResult:
    keyword_recall: float
    hit_keywords: list[str]
    total_keywords: int
    correct: bool
    cited: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "keyword_recall": round(self.keyword_recall, 4),
            "hit_keywords": self.hit_keywords,
            "total_keywords": self.total_keywords,
            "correct": self.correct,
            "cited": self.cited,
        }


def grade_deterministic(expected: str, response: str, *, threshold: float = DEFAULT_THRESHOLD) -> DeterministicResult:
    ks = keywords(expected)
    low = response.lower()
    hit = [k for k in ks if k.lower() in low]
    recall = len(hit) / len(ks) if ks else 0.0
    return DeterministicResult(
        keyword_recall=recall,
        hit_keywords=hit,
        total_keywords=len(ks),
        correct=bool(ks) and recall >= threshold,
        cited=bool(_CITATION.search(response)),
    )


def retrieval_hit(source_path: str, retrieved_paths: list[str], *, top: int | None = None) -> bool:
    """出典ファイルが検索結果(上位 top 件)に含まれるか。

    コーパス側のパスは `local_llm/docs/x.md` のように接頭辞が付くので、末尾一致で判定する。
    """
    target = source_path.replace("\\", "/").lstrip("./")
    if not target:
        return False
    paths = retrieved_paths if top is None else retrieved_paths[:top]
    for p in paths:
        q = p.replace("\\", "/")
        if q == target or q.endswith("/" + target):
            return True
    return False


# ---------------------------------------------------------------- LLM 採点

GRADER_SYSTEM_PROMPT = (
    "あなたは技術文書に関する Q&A の採点者です。質問、模範解答、受験者の回答が与えられます。"
    "模範解答と照らして受験者の回答の正確さを 1〜5 の整数で採点してください。"
    "5: 模範解答と同じ事実をすべて含み誤りがない。4: 主要な事実は正しいが細部が欠ける。"
    "3: 半分程度は正しいが重要な欠落か軽い誤りがある。2: 関係はあるが大部分が誤りか空疎。"
    "1: 無関係、または明確に誤った事実を述べている。"
    "文体や長さは評価せず、事実の一致だけを見てください。理由は日本語で 1 文にしてください。"
)

GRADER_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "minimum": LLM_SCORE_MIN, "maximum": LLM_SCORE_MAX},
        "reason": {"type": "string", "maxLength": 300},
    },
    "required": ["score", "reason"],
    "additionalProperties": False,
}

GRADER_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {"name": "grade", "strict": True, "schema": GRADER_JSON_SCHEMA},
}


def build_grader_messages(question: str, expected: str, response: str) -> list[Message]:
    user = (
        f"## 質問\n{question}\n\n## 模範解答\n{expected}\n\n## 受験者の回答\n{response or '(空の回答)'}\n\n"
        '{"score": <1〜5 の整数>, "reason": "<理由 1 文>"} の JSON だけを出力してください。'
    )
    return [Message("system", GRADER_SYSTEM_PROMPT), Message("user", user)]


@dataclass(frozen=True)
class LlmGrade:
    score: int | None
    reason: str
    raw: str

    def to_dict(self) -> dict[str, Any]:
        return {"score": self.score, "reason": self.reason, "raw": self.raw}


def parse_llm_grade(text: str) -> LlmGrade:
    """採点モデルの出力(JSON のはず)を読む。壊れていたら score=None にして理由に残す(採点失敗を隠さない)。"""
    raw = text.strip()
    candidate = raw
    if not candidate.startswith("{"):
        m = re.search(r"\{.*\}", candidate, flags=re.S)
        candidate = m.group(0) if m else candidate
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        return LlmGrade(score=None, reason="採点出力が JSON として読めません", raw=raw)
    score = data.get("score") if isinstance(data, dict) else None
    if isinstance(score, bool) or not isinstance(score, int) or not (LLM_SCORE_MIN <= score <= LLM_SCORE_MAX):
        return LlmGrade(score=None, reason=f"score が 1〜5 の整数ではありません: {score!r}", raw=raw)
    reason = str(data.get("reason") or "").strip()
    return LlmGrade(score=score, reason=reason, raw=raw)
