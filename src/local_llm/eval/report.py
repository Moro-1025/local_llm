"""結果の書き出し(JSON / Markdown)と、複数構成の比較表。

結果 JSON の形は eval/schemas/qa-result.schema.json に書いてある。grader.model と answer.model が
別であることは runner が実行前に拒否し、JSON には `grader.differs_from_answer_model: true` を記録する
(schema は const true を要求し、scripts/agent/check_eval_results.py が文字列比較で裏を取る)。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

RESULT_SCHEMA_VERSION = "1.0"
DEFAULT_RESULTS_DIR = Path("eval/results")


def result_basename(started_at_compact: str, suite: str, config_id: str) -> str:
    return f"{started_at_compact}-{suite}-{config_id}"


def write_result(result: dict[str, Any], out_dir: Path, basename: str) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{basename}.json"
    md_path = out_dir / f"{basename}.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(result), encoding="utf-8")
    return json_path, md_path


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def _num(value: float | None, digits: int = 2) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def render_markdown(result: dict[str, Any]) -> str:
    cfg = result["config"]
    m = result["metrics"]
    ds = result["dataset"]
    rag_note = f"あり top_k={cfg['top_k']}" if cfg["use_rag"] else "なし"
    lines = [
        f"# 評価結果: suite={result['suite']} / 構成 {cfg['id']}({cfg['label']})",
        "",
        f"- 回答モデル: `{result['answer']['model']}`(RAG {rag_note})",
        f"- 採点モデル: `{result['grader']['model']}`(回答モデルと別: {result['grader']['differs_from_answer_model']})",
        f"- データセット: `{ds['path']}`(version {ds['version']}、全 {ds['total']} 件のうち {ds['n']} 件、seed {ds['seed']})",
        f"- llama-server: `{result['server']['base_url']}`",
        f"- 実行: {result['started_at']} 〜 {result['finished_at']}",
        "",
        "## 指標",
        "",
        "| 指標 | 値 |",
        "| --- | --- |",
        f"| 正答率(キーワード再現率 ≥ {m['threshold']}) | {m['deterministic_correct']} / {m['n']} = {_pct(m['deterministic_accuracy'])} |",
        f"| キーワード再現率(平均) | {_num(m['mean_keyword_recall'], 3)} |",
        f"| LLM 採点(1〜5、平均) | {_num(m['llm_mean_score'])}({m['llm_scored']} 件採点、失敗 {m['llm_failed']} 件) |",
        f"| 引用 [n] を含む応答 | {_pct(m['citation_rate'])} |",
        f"| 検索ヒット率 top-3 / top-k | {_pct(m['retrieval_hit_rate_top3'])} / {_pct(m['retrieval_hit_rate_topk'])} |",
        f"| 生成速度(tok/s、平均) | {_num(m['mean_tok_s'], 1)} |",
        f"| 1 問あたり秒(平均) | {_num(m['mean_sec'], 1)} |",
        "",
        "## 各問",
        "",
        "| # | 正答 | 再現率 | LLM | 質問 | 出典 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for it in result["items"]:
        d = it["deterministic"]
        llm = it["llm"]["score"] if it.get("llm") else None
        q = it["question"].replace("|", "\\|")
        lines.append(
            f"| {it['index']} | {'OK' if d['correct'] else 'NG'} | {d['keyword_recall']:.2f} | "
            f"{llm if llm is not None else '-'} | {q} | {it['source']} |"
        )
    lines.append("")
    return "\n".join(lines)


def compute_metrics(
    items: list[dict[str, Any]], *, threshold: float, use_rag: bool, top_k: int | None
) -> dict[str, Any]:
    n = len(items)
    det = [it["deterministic"] for it in items]
    correct = sum(1 for d in det if d["correct"])
    llm_scores = [it["llm"]["score"] for it in items if it.get("llm") and it["llm"]["score"] is not None]
    llm_failed = sum(1 for it in items if it.get("llm") and it["llm"]["score"] is None)
    tok_s = [it["timing"]["tok_s"] for it in items if it["timing"].get("tok_s")]
    secs = [it["timing"]["sec"] for it in items]
    hits3 = [it["retrieval"]["hit_top3"] for it in items if it.get("retrieval")]
    hitsk = [it["retrieval"]["hit_topk"] for it in items if it.get("retrieval")]
    return {
        "n": n,
        "threshold": threshold,
        "deterministic_correct": correct,
        "deterministic_accuracy": round(correct / n, 4) if n else None,
        "mean_keyword_recall": round(sum(d["keyword_recall"] for d in det) / n, 4) if n else None,
        "llm_scored": len(llm_scores),
        "llm_failed": llm_failed,
        "llm_mean_score": round(sum(llm_scores) / len(llm_scores), 3) if llm_scores else None,
        "citation_rate": round(sum(1 for d in det if d["cited"]) / n, 4) if n else None,
        "retrieval_hit_rate_top3": round(sum(hits3) / len(hits3), 4) if (use_rag and hits3) else None,
        "retrieval_hit_rate_topk": round(sum(hitsk) / len(hitsk), 4) if (use_rag and hitsk) else None,
        "top_k": top_k if use_rag else None,
        "mean_tok_s": round(sum(tok_s) / len(tok_s), 1) if tok_s else None,
        "mean_sec": round(sum(secs) / len(secs), 2) if secs else None,
    }


# ---------------------------------------------------------------- 比較表(README に転記する)

COMPARISON_HEADER = (
    "| 構成 | モデル | RAG | 正答率(決定論) | キーワード再現率 | LLM 採点(1〜5) | 引用率 | 検索ヒット top-3 | tok/s | 結果ファイル |\n"
    "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"
)


def comparison_rows(results: list[tuple[Path, dict[str, Any]]]) -> list[str]:
    rows = []
    for path, r in sorted(results, key=lambda pr: pr[1]["config"]["id"]):
        cfg, m = r["config"], r["metrics"]
        rows.append(
            f"| {cfg['id']} | `{r['answer']['model']}` | {'あり' if cfg['use_rag'] else 'なし'} | "
            f"{m['deterministic_correct']} / {m['n']}({_pct(m['deterministic_accuracy'])}) | "
            f"{_num(m['mean_keyword_recall'], 3)} | {_num(m['llm_mean_score'])} | {_pct(m['citation_rate'])} | "
            f"{_pct(m['retrieval_hit_rate_top3'])} | {_num(m['mean_tok_s'], 1)} | `{path.name}` |"
        )
    return rows


def render_comparison(results: list[tuple[Path, dict[str, Any]]]) -> str:
    return COMPARISON_HEADER + "\n" + "\n".join(comparison_rows(results)) + "\n"


def load_results(paths: list[Path]) -> list[tuple[Path, dict[str, Any]]]:
    return [(p, json.loads(p.read_text(encoding="utf-8"))) for p in paths]


def latest_per_config(results_dir: Path, suite: str) -> list[Path]:
    """構成ごとに最新(ファイル名の日時が最大)の結果 JSON を 1 つずつ返す。"""
    latest: dict[str, Path] = {}
    for p in sorted(results_dir.glob(f"*-{suite}-*.json")):
        config_id = p.stem.rsplit("-", 1)[-1]
        latest[config_id] = p  # ソート済みなので後のものが新しい
    return [latest[k] for k in sorted(latest)]
