"""`llm-eval` の入口。

    uv run llm-eval --suite qa --config A                 # 構成 A を全件(147 問)で評価
    uv run llm-eval --suite qa --config B --n 40 --seed 7 # seed 固定の部分集合
    uv run llm-eval --suite qa --config D --no-llm-grade  # 決定論的採点だけ(採点モデル無しで動かすとき)
    uv run llm-eval --summarize                           # eval/results/ の構成ごと最新結果から比較表を出す

結果は eval/results/<日時>-<suite>-<構成>.json と .md。採点モデルは --grader-model か LLM_GRADER_MODEL
(既定 unsloth/Qwen3-8B-GGUF:Q4_K_M)。回答モデルと同じなら実行せず終了コード 2。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

from local_llm.client import LlamaClient, LlamaClientError
from local_llm.config import load_settings
from local_llm.eval.configs import CONFIGS, default_grader_model, resolve_config
from local_llm.eval.dataset import load_dataset
from local_llm.eval.report import (
    DEFAULT_RESULTS_DIR,
    latest_per_config,
    load_results,
    render_comparison,
    result_basename,
    write_result,
)
from local_llm.eval.runner import EvalRunner, GraderSeparationError, RunOptions
from local_llm.rag.embedder import Embedder
from local_llm.rag.retriever import Retriever
from local_llm.rag.store import RagStore

SUITES = {"qa": Path("eval/datasets/qa-v1.jsonl")}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="llm-eval", description="5 構成(A〜E)を同じデータセットで評価する")
    p.add_argument("--suite", choices=sorted(SUITES), default="qa")
    p.add_argument("--config", help="構成 id: " + " / ".join(f"{k}={v.label}" for k, v in CONFIGS.items()))
    p.add_argument("--dataset", type=Path, default=None, help="データセット JSONL(既定は suite に対応するもの)")
    p.add_argument("--n", type=int, default=None, help="使う問題数(省略時は全件)")
    p.add_argument("--seed", type=int, default=42, help="部分集合を選ぶ seed(既定 42)")
    p.add_argument("--top-k", type=int, default=None, help="RAG あり構成で差し込む資料数(既定 5)")
    p.add_argument("--grader-model", default=None, help="採点モデル id(既定 LLM_GRADER_MODEL)")
    p.add_argument("--large-model", default=None, help="構成 A/B の大型モデル id(既定 LLM_DEFAULT_MODEL)")
    p.add_argument("--base-url", default=None, help="llama-server の URL(既定 LLAMA_BASE_URL)")
    p.add_argument("--db", type=Path, default=None, help="RAG 索引の SQLite(既定 CHAT_DB_PATH)")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    p.add_argument("--threshold", type=float, default=0.5, help="正答とみなすキーワード再現率(既定 0.5)")
    p.add_argument("--max-tokens", type=int, default=400, help="回答の最大トークン数(既定 400)")
    p.add_argument("--no-llm-grade", action="store_true", help="LLM 採点を行わない(決定論的採点のみ)")
    p.add_argument("--no-manage-models", action="store_true", help="ルーターの load/unload を使わない")
    p.add_argument("--summarize", action="store_true", help="結果ファイルから比較表(Markdown)を出力して終了")
    return p.parse_args(argv)


def summarize(out_dir: Path, suite: str) -> int:
    paths = latest_per_config(out_dir, suite)
    if not paths:
        print(f"{out_dir} に {suite} の結果がありません", file=sys.stderr)
        return 1
    print(render_comparison(load_results(paths)), end="")
    return 0


async def run(args: argparse.Namespace) -> int:
    settings = load_settings()
    base_url = (args.base_url or settings.base_url).rstrip("/")
    grader_model = args.grader_model or default_grader_model()
    cfg = resolve_config(args.config, large_model=args.large_model or settings.default_model, top_k=args.top_k)
    dataset = load_dataset(args.dataset or SUITES[args.suite])

    def log(msg: str) -> None:
        print(msg, flush=True)

    started_compact = datetime.now().strftime("%Y%m%d-%H%M%S")
    basename = result_basename(started_compact, args.suite, cfg.id)
    options = RunOptions(
        n=args.n,
        seed=args.seed,
        threshold=args.threshold,
        answer_max_tokens=args.max_tokens,
        llm_grade=not args.no_llm_grade,
        manage_models=not args.no_manage_models,
        partial_path=args.out_dir / f"{basename}.partial.json",
        log=log,
    )

    async with LlamaClient(base_url, timeout=settings.timeout) as client:
        retriever = None
        if cfg.use_rag:
            store = RagStore(args.db or settings.db_path)
            retriever = Retriever(store, Embedder(client, settings.embed_model))
            ids, _ = store.load_embeddings(settings.embed_model)
            if not ids:
                print("RAG の索引が空です。先に `uv run llm-index` を実行してください", file=sys.stderr)
                return 1
            log(f"RAG 索引: {len(ids)} チャンク({settings.embed_model})")
        runner = EvalRunner(client, cfg, grader_model=grader_model, retriever=retriever, options=options)
        log(f"構成 {cfg.id}({cfg.label}): 回答 {cfg.answer_model} / 採点 {grader_model} / "
            f"{dataset.path.name} {args.n or dataset.total} 件 seed {args.seed} / {base_url}")
        result = await runner.run(dataset, suite=args.suite)

    json_path, md_path = write_result(result, args.out_dir, basename)
    if options.partial_path and options.partial_path.exists():
        options.partial_path.unlink()
    m = result["metrics"]
    log(
        f"完了: 正答率 {m['deterministic_correct']}/{m['n']} "
        f"LLM 採点平均 {m['llm_mean_score']} -> {json_path} / {md_path}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.summarize:
        return summarize(args.out_dir, args.suite)
    if not args.config:
        print("--config A〜E を指定してください(比較表だけなら --summarize)", file=sys.stderr)
        return 2
    try:
        return asyncio.run(run(args))
    except GraderSeparationError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 2
    except (ValueError, FileNotFoundError, LlamaClientError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("中断しました(途中経過は *.partial.json)", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
