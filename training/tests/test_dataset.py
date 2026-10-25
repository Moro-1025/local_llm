"""LORA-002 の検証: qa.jsonl の件数と schema、qa.meta.json の内容、train/eval の分割。
加えて、チャンク化とフィルタ(決定論的な部分)の単体テスト。

    cd training && ./run_tests.sh
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

TRAINING = Path(__file__).resolve().parents[1]
ROOT = TRAINING.parent
sys.path.insert(0, str(TRAINING / "data"))

import build_dataset as bd  # noqa: E402
import split as sp  # noqa: E402

QA = TRAINING / "data" / "qa.jsonl"
META = TRAINING / "data" / "qa.meta.json"
TRAIN = TRAINING / "data" / "train.jsonl"
EVAL = TRAINING / "data" / "eval.jsonl"
EVAL_V1 = ROOT / "eval" / "datasets" / "qa-v1.jsonl"

needs_data = pytest.mark.skipif(not QA.exists(), reason="qa.jsonl が無い(build_dataset.py を先に実行)")


def read_jsonl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


# ------------------------------------------------------------ 単体: チャンク化
def test_split_sections_keeps_heading_and_body():
    text = "intro\n\n## A\nbody a\n\n### B\nbody b\n"
    assert bd.split_sections(text) == [("", "intro"), ("A", "body a"), ("B", "body b")]


def test_chunk_markdown_merges_short_and_splits_long():
    short = "## S1\n" + "x" * 50 + "\n\n## S2\n" + "y" * 50 + "\n\n## S3\n" + "z" * 400
    chunks = bd.chunk_markdown("d.md", short, min_chars=300, max_chars=1000)
    assert len(chunks) == 1 and chunks[0].source == "d.md#S1"  # 短い節が結合される
    long = "## L\n" + "\n\n".join("p" * 300 for _ in range(5))
    chunks = bd.chunk_markdown("d.md", long, min_chars=100, max_chars=700)
    assert len(chunks) == 3 and all(len(c.text) <= 700 for c in chunks)
    assert chunks[1].source == "d.md#L (2)"


def test_chunking_is_deterministic():
    a = bd.build_chunks(ROOT, min_chars=400, max_chars=1800)
    b = bd.build_chunks(ROOT, min_chars=400, max_chars=1800)
    assert a == b and len(a) > 0


# ------------------------------------------------------------ 単体: フィルタ
def test_has_source_term():
    chunk = "models.ini に [*] セクションを書く。ctx-size は 8192。"
    assert bd.has_source_term("models.ini の役割は何ですか?", chunk)
    assert bd.has_source_term("ctx-size の既定値は?", chunk)
    assert not bd.has_source_term("これは何ですか?", chunk)


def test_filter_pairs_drops_bad_rows():
    chunk = "verify.ps1 は検証の単一入口で、pytest と git diff --check を走らせる。"
    rows = [
        {"question": "verify.ps1 は何をするスクリプトですか?", "answer": "検証の単一入口で pytest と git diff --check を実行する。", "source": "s", "chunk_text": chunk},
        {"question": "verify.ps1 は何をするスクリプトですか?", "answer": "同じ質問なので重複として落ちるはずの行。", "source": "s", "chunk_text": chunk},
        {"question": "verify.ps1 は何を走らせますか?", "answer": "短い。", "source": "s", "chunk_text": chunk},
        {"question": "短い?", "answer": "質問が短すぎるので落ちるはずの行、答えは十分に長い。", "source": "s", "chunk_text": chunk},
        {"question": "このプロジェクトの目的は何ですか?", "answer": "出典語が無いので落ちるはずの行、答えは十分に長い。", "source": "s", "chunk_text": chunk},
    ]
    kept, reasons = bd.filter_pairs(rows)
    assert [k["question"] for k in kept] == ["verify.ps1 は何をするスクリプトですか?"]
    assert reasons == {"short_answer": 1, "short_question": 1, "no_source_term": 1, "duplicate": 1}
    assert set(kept[0]) == {"question", "answer", "source"}


def test_split_is_deterministic_and_disjoint():
    rows = [{"question": f"q{i}", "answer": "a", "source": "s"} for i in range(100)]
    t1, e1 = sp.split(rows, eval_ratio=0.1, seed=42)
    t2, e2 = sp.split(rows, eval_ratio=0.1, seed=42)
    assert (t1, e1) == (t2, e2)
    assert len(e1) == 10 and len(t1) == 90
    assert not ({r["question"] for r in t1} & {r["question"] for r in e1})


# ------------------------------------------------------------ 実データ: LORA-002
@needs_data
def test_qa_jsonl_count_and_schema():
    rows = read_jsonl(QA)
    assert len(rows) >= 300, f"件数不足: {len(rows)}"
    for r in rows:
        assert set(r) == {"question", "answer", "source"}
        assert all(isinstance(r[k], str) and r[k].strip() for k in r)
        assert len(r["answer"]) >= bd.MIN_ANSWER_CHARS
        assert len(r["question"]) >= bd.MIN_QUESTION_CHARS
        assert "#" in r["source"]  # "path#見出し"
    keys = [bd.normalize_question(r["question"]) for r in rows]
    assert len(keys) == len(set(keys)), "質問が重複している"


@needs_data
def test_qa_meta_has_provenance():
    meta = json.loads(META.read_text(encoding="utf-8"))
    for k in ("system_prompt", "user_prompt_template", "model", "started_at", "finished_at", "counts", "sources"):
        assert k in meta, f"qa.meta.json に {k} が無い"
    assert meta["model"].startswith("unsloth/")
    assert meta["counts"]["kept"] == len(read_jsonl(QA))
    assert meta["sources"], "出典ファイル一覧が空"


@needs_data
def test_train_eval_split_matches_and_eval_copied():
    assert TRAIN.exists() and EVAL.exists() and EVAL_V1.exists(), "split.py を実行していない"
    rows, train, evals = read_jsonl(QA), read_jsonl(TRAIN), read_jsonl(EVAL)
    assert len(train) + len(evals) == len(rows)
    tq = {r["question"] for r in train}
    eq = {r["question"] for r in evals}
    assert not (tq & eq), "train と eval に同じ質問がある"
    assert abs(len(evals) / len(rows) - 0.1) < 0.02
    assert read_jsonl(EVAL_V1) == evals, "eval/datasets/qa-v1.jsonl が eval.jsonl と一致しない"
