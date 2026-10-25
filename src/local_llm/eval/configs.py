"""比較する構成(A〜E)の定義。

モデル id は llama-server のルーターモードが `/v1/models` で公開する名前(models.ini のセクション名から
"UD-" を落としたもの)。大型モデルは設定(LLM_DEFAULT_MODEL)に従うので、既定を替えれば A/B も追従する。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace

from local_llm.config import DEFAULT_MODEL

# 小型モデル(Phase 5 で models.ini に登録済み)
SMALL_BASE_MODEL = "unsloth/Qwen3-4B-GGUF:Q4_K_M"
LORA_MODEL = "local/qwen3-4b-localllm-lora"
# 採点モデルの既定。回答モデル(35B-A3B / 4B / 4B+LoRA)のどれとも別で、12 GB に 1 つで載る dense 8B。
# 環境変数 LLM_GRADER_MODEL で差し替えられる。
DEFAULT_GRADER_MODEL = "unsloth/Qwen3-8B-GGUF:Q4_K_M"


@dataclass(frozen=True)
class EvalConfig:
    """1 構成 = 回答モデル × RAG の有無。"""

    id: str
    label: str
    answer_model: str
    use_rag: bool
    intent: str
    top_k: int = 5

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "answer_model": self.answer_model,
            "use_rag": self.use_rag,
            "top_k": self.top_k if self.use_rag else None,
            "intent": self.intent,
        }


def build_configs(large_model: str = DEFAULT_MODEL) -> dict[str, EvalConfig]:
    return {
        "A": EvalConfig("A", "素の大型モデル", large_model, False, "大型モデルの素の知識"),
        "B": EvalConfig("B", "大型モデル + RAG", large_model, True, "RAG の効果"),
        "C": EvalConfig("C", "素の小型モデル(Qwen3-4B)", SMALL_BASE_MODEL, False, "小型の基準"),
        "D": EvalConfig("D", "Qwen3-4B + LoRA", LORA_MODEL, False, "LoRA の効果"),
        "E": EvalConfig("E", "Qwen3-4B + LoRA + RAG", LORA_MODEL, True, "併用"),
    }


CONFIGS = build_configs()


def resolve_config(config_id: str, *, large_model: str | None = None, top_k: int | None = None) -> EvalConfig:
    """構成 id(大文字小文字は無視)から EvalConfig を返す。未知なら ValueError。"""
    key = config_id.strip().upper()
    configs = build_configs(large_model) if large_model else CONFIGS
    if key not in configs:
        raise ValueError(f"未知の構成 {config_id!r}(使えるのは {', '.join(configs)})")
    cfg = configs[key]
    if top_k is not None and cfg.use_rag:
        cfg = replace(cfg, top_k=top_k)
    return cfg


def default_grader_model() -> str:
    return os.environ.get("LLM_GRADER_MODEL", DEFAULT_GRADER_MODEL)
