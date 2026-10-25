"""決定論的採点・LLM 採点のパース・データセット読み込み(GPU も HTTP も不要)。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from local_llm.eval.configs import CONFIGS, DEFAULT_GRADER_MODEL, resolve_config
from local_llm.eval.dataset import load_dataset, parse_dataset_name, select_items
from local_llm.eval.graders import (
    build_grader_messages,
    grade_deterministic,
    keywords,
    parse_llm_grade,
    retrieval_hit,
)


def test_keywords_and_recall():
    expected = "uv run pytest を実行します。respx でモックします。"
    ks = keywords(expected)
    assert "pytest" in ks and "respx" in ks and "実行" not in ks  # 一般語は除外
    full = grade_deterministic(expected, "GPU なしなら uv run pytest を実行、respx でモックする")
    assert full.correct and full.keyword_recall == 1.0 and not full.cited
    none = grade_deterministic(expected, "わかりません")
    assert not none.correct and none.keyword_recall == 0.0
    assert grade_deterministic("答え", "資料によると [1] です").cited


def test_recall_threshold_boundary():
    expected = "SQLite に float32 の BLOB で保存し numpy で総当たりする。"
    ks = keywords(expected)
    assert len(ks) >= 4
    half = " ".join(ks[: len(ks) // 2])
    r = grade_deterministic(expected, half, threshold=0.5)
    assert r.correct == (r.keyword_recall >= 0.5)


def test_retrieval_hit_suffix_match():
    paths = ["local_llm/docs/internal-spec.md", "ai_harness/docs/core/glossary.md"]
    assert retrieval_hit("docs/internal-spec.md", paths)
    assert retrieval_hit("docs\\internal-spec.md", paths)
    assert not retrieval_hit("docs/internal-spec.md", paths, top=0)
    assert retrieval_hit("docs/core/glossary.md", paths, top=2)
    assert not retrieval_hit("docs/core/glossary.md", paths, top=1)
    assert not retrieval_hit("spec.md", paths)  # 部分一致ではなく末尾のパス要素一致


@pytest.mark.parametrize(
    ("text", "score"),
    [
        ('{"score": 4, "reason": "ok"}', 4),
        ('前置き {"score": 1, "reason": "x"} 後置き', 1),
        ('{"score": 7, "reason": "範囲外"}', None),
        ('{"score": "3", "reason": "文字列"}', None),
        ("壊れた出力", None),
    ],
)
def test_parse_llm_grade(text, score):
    g = parse_llm_grade(text)
    assert g.score == score
    assert g.raw == text.strip()
    if score is None:
        assert g.reason  # 失敗理由が残る


def test_grader_messages_contain_all_parts():
    msgs = build_grader_messages("Q?", "正解", "回答")
    assert msgs[0].role == "system" and "1〜5" in msgs[0].content
    assert "## 質問\nQ?" in msgs[1].content and "## 模範解答\n正解" in msgs[1].content and "## 受験者の回答\n回答" in msgs[1].content


def test_dataset_load_and_sampling(tmp_path: Path):
    p = tmp_path / "qa-v3.jsonl"
    rows = [{"question": f"q{i}", "answer": f"a{i}", "source": f"docs/x.md#{i}"} for i in range(10)]
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    ds = load_dataset(p)
    assert (ds.name, ds.version, ds.total) == ("qa", 3, 10)
    assert ds.items[3].source_path == "docs/x.md"

    a = select_items(ds, 4, seed=42)
    b = select_items(ds, 4, seed=42)
    c = select_items(ds, 4, seed=7)
    assert [x.index for x in a] == [x.index for x in b]  # seed 固定で再現
    assert [x.index for x in a] == sorted(x.index for x in a)  # 元の順序を保つ
    assert [x.index for x in a] != [x.index for x in c] or len(set(range(10))) == 4
    assert len(select_items(ds, None, 1)) == 10 and len(select_items(ds, 99, 1)) == 10
    with pytest.raises(ValueError):
        select_items(ds, 0, 1)


def test_dataset_name_must_be_versioned(tmp_path: Path):
    with pytest.raises(ValueError):
        parse_dataset_name(Path("qa.jsonl"))
    bad = tmp_path / "qa-v1.jsonl"
    bad.write_text('{"question": "q", "answer": ""}\n', encoding="utf-8")
    with pytest.raises(ValueError):
        load_dataset(bad)


def test_configs_cover_five_and_grader_differs():
    assert sorted(CONFIGS) == ["A", "B", "C", "D", "E"]
    assert CONFIGS["A"].answer_model == CONFIGS["B"].answer_model and CONFIGS["B"].use_rag
    assert CONFIGS["D"].answer_model == CONFIGS["E"].answer_model and CONFIGS["E"].use_rag
    assert not CONFIGS["C"].use_rag and CONFIGS["C"].answer_model != CONFIGS["D"].answer_model
    for cfg in CONFIGS.values():
        assert cfg.answer_model != DEFAULT_GRADER_MODEL
    assert resolve_config("b", large_model="big").answer_model == "big"
    assert resolve_config("e", top_k=3).top_k == 3
    with pytest.raises(ValueError):
        resolve_config("Z")
