"""Phase 6: 評価ハーネス(`uv run llm-eval`)。

- configs  : 比較する 5 構成(A 素の大型 / B +RAG / C 素の小型 / D LoRA / E LoRA+RAG)と採点モデル
- dataset  : eval/datasets/<name>-v<N>.jsonl の読み込みと seed 固定の部分集合
- graders  : 決定論的採点(キーワード再現率、引用の有無、検索ヒット)と LLM 採点(1〜5)
- runner   : 回答 → 採点の 2 段を llama-server(ルーターモード)に対して実行する
- report   : 結果を eval/results/<日時>-<suite>-<構成>.json / .md に書き、比較表を作る
- cli      : `llm-eval` の入口

採点モデルは回答モデルと別にする(契約 GRADER_SEPARATION)。runner は同じなら実行を拒否し、
結果 JSON は eval/schemas/qa-result.schema.json で検証できる。
"""

from local_llm.eval.configs import CONFIGS, EvalConfig, resolve_config

__all__ = ["CONFIGS", "EvalConfig", "resolve_config"]
