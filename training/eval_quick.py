"""暫定評価(Phase 5 / LORA-005、required=false)。eval 質問 N 件を llama-server の複数モデルに投げ、
正解の答えに含まれる「キーワード」がどれだけ応答に出るかで簡易正答率を出す。Phase 6 の採点器ができるまでの仮。

    uv run python training/eval_quick.py --models local/qwen3-4b-localllm-lora unsloth/Qwen3-4B-GGUF:Q4_K_M

採点(決定論的): 正解文から英数字 3 字以上の語とカタカナ/漢字 2 字以上の連続を取り、
応答にそのうち 50% 以上が含まれていれば正答。結果は training/runs/eval-<日時>.json と Markdown 表に出す。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DEFAULT_BASE_URL = os.environ.get("LLAMA_BASE_URL", "http://127.0.0.1:8080")
_ASCII = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./\-]{2,}")
_CJK = re.compile(r"[゠-ヿ一-鿿々]{2,}")
STOP = {"こと", "ため", "必要", "場合", "使用", "設定", "実行", "可能", "内容", "指定", "存在", "対応", "確認", "方法", "行う", "など", "また", "この", "その", "よう", "される", "ます", "です", "いる", "ある", "する"}


def keywords(answer: str) -> list[str]:
    ks = {k.lower() for k in _ASCII.findall(answer)} | {k for k in _CJK.findall(answer) if k not in STOP}
    return sorted(ks)


def score(answer: str, response: str) -> tuple[float, list[str]]:
    ks = keywords(answer)
    if not ks:
        return 0.0, []
    low = response.lower()
    hit = [k for k in ks if k.lower() in low]
    return len(hit) / len(ks), hit


def ask(client, base_url: str, model: str, system: str, question: str) -> tuple[str, float]:
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": question}],
        "temperature": 0.0,
        "max_tokens": 400,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    t0 = time.time()
    r = client.post(f"{base_url}/v1/chat/completions", json=body, timeout=600)
    r.raise_for_status()
    msg = r.json()["choices"][0]["message"]
    content = re.sub(r"<think>.*?</think>", "", msg.get("content") or "", flags=re.S).strip()
    return content, time.time() - t0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--dataset", default=str(ROOT / "eval" / "datasets" / "qa-v1.jsonl"))
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--unload-between", action="store_true", help="モデルごとの評価後に /models/unload で VRAM を空ける")
    ap.add_argument("--system", default="あなたは local_llm プロジェクトと ai_harness のドキュメントに詳しいアシスタントです。日本語で簡潔に答えてください。")
    args = ap.parse_args(argv)

    import httpx

    rows = [json.loads(l) for l in Path(args.dataset).read_text(encoding="utf-8").splitlines() if l.strip()][: args.n]
    results: dict[str, list[dict]] = {}
    partial = HERE / "runs" / "eval-partial.json"
    partial.parent.mkdir(exist_ok=True)
    with httpx.Client() as client:
        for model in args.models:
            # 初回はモデルのダウンロード/ロードに時間がかかるので、先にロードを済ませてから計測する
            client.post(f"{args.base_url}/models/load", json={"model": model}, timeout=1800)
            for _ in range(600):
                st = {m["id"]: m for m in client.get(f"{args.base_url}/models", timeout=30).json().get("data", [])}
                s = st.get(model, {}).get("status", {})
                if (s.get("value") if isinstance(s, dict) else s) == "loaded":
                    break
                time.sleep(2)
            out = []
            for i, r in enumerate(rows, 1):
                resp, sec = ask(client, args.base_url, model, args.system, r["question"])
                s, hit = score(r["answer"], resp)
                out.append({"question": r["question"], "expected": r["answer"], "response": resp, "score": round(s, 3), "hit": hit, "correct": s >= args.threshold, "sec": round(sec, 1)})
                print(f"[{model}] {i}/{len(rows)} score={s:.2f} {'OK' if s >= args.threshold else 'NG'} ({sec:.1f}s)", flush=True)
            results[model] = out
            partial.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")  # 途中経過(失敗時の保険)
            if args.unload_between:
                # ルーターモードの /models/unload で VRAM を空けてから次のモデルへ(12 GB に 2 つ同時に載せない)
                client.post(f"{args.base_url}/models/unload", json={"model": model}, timeout=60)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    summary = {m: {"n": len(v), "correct": sum(x["correct"] for x in v), "accuracy": round(sum(x["correct"] for x in v) / len(v), 3), "mean_keyword_recall": round(sum(x["score"] for x in v) / len(v), 3)} for m, v in results.items()}
    out_json = HERE / "runs" / f"eval-{stamp}.json"
    out_json.parent.mkdir(exist_ok=True)
    out_json.write_text(json.dumps({"dataset": args.dataset, "n": len(rows), "threshold": args.threshold, "summary": summary, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n| モデル | 正答 / N | 正答率 | キーワード再現率(平均) |\n| --- | --- | --- | --- |")
    for m, s in summary.items():
        print(f"| {m} | {s['correct']} / {s['n']} | {s['accuracy']:.0%} | {s['mean_keyword_recall']:.2f} |")
    print(f"\n-> {out_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
