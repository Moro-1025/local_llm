"""RAG-002: 検索精度の評価(tests/rag/eval_retrieval.jsonl)。

各質問で top_k 件を検索し、正解文書(expect_paths)かつ expect_keywords のいずれかを
heading か本文に含むチャンクが入っていれば hit とする。llama-server(埋め込みモデル)が
動いている必要があるので pytest には含めず、手動で実行する:

    uv run python scripts/agent/eval_retrieval.py            # 既定: top_k=3, 合格 8/10
    uv run python scripts/agent/eval_retrieval.py --json out.json

終了コードは合格なら 0、不合格なら 1。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from local_llm.client import LlamaClient, LlamaClientError
from local_llm.config import load_settings
from local_llm.rag.embedder import Embedder
from local_llm.rag.retriever import Retriever
from local_llm.rag.store import RagStore

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES = ROOT / "tests" / "rag" / "eval_retrieval.jsonl"


def load_cases(path: Path) -> list[dict]:
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            cases.append(json.loads(line))
    return cases


def is_hit(case: dict, hits: list) -> tuple[bool, int | None]:
    """(hit したか, 最初に当たった順位 1 始まり) を返す。"""
    paths = set(case.get("expect_paths", []))
    keywords = case.get("expect_keywords", [])
    for h in hits:
        # インデックスの path はコーパスルートからの相対(例: ai_harness/docs/...)なので後方一致で見る
        path_ok = any(h.path == p or h.path.endswith("/" + p) or h.path.endswith(p) for p in paths)
        if not path_ok:
            continue
        if not keywords or any(k.lower() in (h.heading + "\n" + h.text).lower() for k in keywords):
            return True, h.n
    return False, None


async def run(args: argparse.Namespace) -> int:
    settings = load_settings()
    cases = load_cases(args.cases)
    store = RagStore(args.db or settings.db_path)
    async with LlamaClient(args.base_url or settings.effective_embed_base_url, timeout=settings.timeout) as client:
        retriever = Retriever(store, Embedder(client, args.embed_model or settings.embed_model))
        status = store.status()
        print(f"index: {status['documents']} docs / {status['chunks']} chunks, model={retriever.embedder.model}")
        rows = []
        for case in cases:
            try:
                hits = await retriever.search(case["question"], top_k=args.top_k)
            except LlamaClientError as exc:
                print(f"埋め込みに失敗: {exc}", file=sys.stderr)
                return 2
            ok, rank = is_hit(case, hits)
            rows.append(
                {
                    "id": case["id"],
                    "hit": ok,
                    "rank": rank,
                    "question": case["question"],
                    "top": [{"n": h.n, "path": h.path, "heading": h.heading, "score": h.score} for h in hits],
                }
            )
            mark = "HIT " if ok else "MISS"
            print(f"{mark} {case['id']} rank={rank if rank else '-'}  {case['question'][:48]}")
            for h in hits:
                print(f"       [{h.n}] {h.score:.3f} {h.path} › {h.heading[:50]}")

    passed = sum(1 for r in rows if r["hit"])
    total = len(rows)
    verdict = "PASS" if passed >= args.min_hits else "FAIL"
    print(f"\n{verdict}: {passed}/{total} hit@{args.top_k} (threshold {args.min_hits})")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(
                {
                    "evaluated_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "embedding_model": retriever.embedder.model,
                    "top_k": args.top_k,
                    "hits": passed,
                    "total": total,
                    "threshold": args.min_hits,
                    "verdict": verdict,
                    "index_status": status,
                    "cases": rows,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"wrote {args.json}")
    return 0 if verdict == "PASS" else 1


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="RAG 検索精度の評価")
    p.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    p.add_argument("--top-k", type=int, default=3)
    p.add_argument("--min-hits", type=int, default=8)
    p.add_argument("--db", type=Path, default=None)
    p.add_argument("--base-url", default=None)
    p.add_argument("--embed-model", default=None)
    p.add_argument("--json", type=Path, default=None, help="結果を JSON で保存するパス")
    sys.exit(asyncio.run(run(p.parse_args(argv))))


if __name__ == "__main__":
    main()
