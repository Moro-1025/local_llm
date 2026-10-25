"""EVAL-001 / EVAL-003: llm-eval が結果 JSON と Markdown を出し、採点モデルと回答モデルが別で記録される(llama-server はモック)。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import respx
from jsonschema import Draft202012Validator, FormatChecker

from local_llm.client import LlamaClient
from local_llm.eval import cli
from local_llm.eval.configs import EvalConfig
from local_llm.eval.dataset import load_dataset
from local_llm.eval.report import latest_per_config, load_results, render_comparison
from local_llm.eval.runner import EvalRunner, GraderSeparationError, RunOptions
from local_llm.rag.embedder import Embedder
from local_llm.rag.retriever import Retriever
from local_llm.rag.store import RagStore

from eval.conftest import ANSWER_MODEL, BASE_URL, EMBED_MODEL, GRADER_MODEL, FakeRouter, mount_router

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "eval" / "schemas" / "qa-result.schema.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


def validate(doc: dict) -> list[str]:
    return [e.message for e in VALIDATOR.iter_errors(doc)]


def _run_cli(argv: list[str], monkeypatch) -> int:
    monkeypatch.setenv("LLM_EMBED_MODEL", EMBED_MODEL)
    monkeypatch.delenv("LLM_GRADER_MODEL", raising=False)
    return cli.main(argv)


@respx.mock(base_url=BASE_URL, assert_all_called=False)
def test_cli_config_a_writes_json_and_markdown(respx_mock, router: FakeRouter, dataset_path: Path, tmp_path: Path, monkeypatch):
    """EVAL-001: --suite qa --config A で JSON と Markdown が出る。Schema に適合し採点モデル ≠ 回答モデル。"""
    mount_router(respx_mock, router)
    out = tmp_path / "results"
    rc = _run_cli(
        ["--suite", "qa", "--config", "A", "--dataset", str(dataset_path), "--base-url", BASE_URL,
         "--large-model", ANSWER_MODEL, "--grader-model", GRADER_MODEL, "--out-dir", str(out)],
        monkeypatch,
    )
    assert rc == 0
    jsons = sorted(out.glob("*-qa-A.json"))
    mds = sorted(out.glob("*-qa-A.md"))
    assert len(jsons) == 1 and len(mds) == 1
    assert not list(out.glob("*.partial.json"))  # 途中経過は完了時に消える

    doc = json.loads(jsons[0].read_text(encoding="utf-8"))
    assert validate(doc) == []
    assert doc["answer"]["model"] == ANSWER_MODEL
    assert doc["grader"]["model"] == GRADER_MODEL
    assert doc["grader"]["differs_from_answer_model"] is True
    assert doc["dataset"]["version"] == 1 and doc["dataset"]["n"] == 3 and doc["dataset"]["total"] == 3
    assert doc["dataset"]["seed"] == 42 and doc["dataset"]["indices"] == [0, 1, 2]

    m = doc["metrics"]
    assert m["n"] == 3 and m["deterministic_correct"] == 2  # 1 問目 OK、2 問目 NG、3 問目 OK(再現率 ≥ 0.5)
    assert m["llm_scored"] == 3 and m["llm_failed"] == 0
    assert m["llm_mean_score"] == pytest.approx((5 + 2 + 2) / 3, abs=1e-3)
    assert m["retrieval_hit_rate_top3"] is None  # RAG なし
    assert m["mean_tok_s"] is not None

    # モデルの載せ替え順: 回答モデル load → unload → 採点モデル load → unload
    assert router.calls == [("load", ANSWER_MODEL), ("unload", ANSWER_MODEL), ("load", GRADER_MODEL), ("unload", GRADER_MODEL)]
    # 回答は temperature 0 / thinking off / max_tokens 付き、採点は json_schema 強制
    answer_reqs = [r for r in router.chat_requests if r["model"] == ANSWER_MODEL]
    grader_reqs = [r for r in router.chat_requests if r["model"] == GRADER_MODEL]
    assert len(answer_reqs) == 3 and len(grader_reqs) == 3
    assert answer_reqs[0]["temperature"] == 0.0 and answer_reqs[0]["max_tokens"] == 400
    assert answer_reqs[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert grader_reqs[0]["response_format"]["type"] == "json_schema"

    md = mds[0].read_text(encoding="utf-8")
    assert "構成 A" in md and f"`{GRADER_MODEL}`" in md and "| 正答率" in md


@respx.mock(base_url=BASE_URL, assert_all_called=False)
def test_cli_rag_config_records_sources_and_citations(respx_mock, router: FakeRouter, dataset_path: Path, rag_db: Path, tmp_path: Path, monkeypatch):
    mount_router(respx_mock, router)
    out = tmp_path / "results"
    rc = _run_cli(
        ["--config", "B", "--dataset", str(dataset_path), "--base-url", BASE_URL, "--db", str(rag_db),
         "--large-model", ANSWER_MODEL, "--grader-model", GRADER_MODEL, "--out-dir", str(out), "--top-k", "2"],
        monkeypatch,
    )
    assert rc == 0
    doc = json.loads(next(out.glob("*-qa-B.json")).read_text(encoding="utf-8"))
    assert validate(doc) == []
    assert doc["config"]["use_rag"] is True and doc["config"]["top_k"] == 2
    items = {it["source"]: it for it in doc["items"]}
    budget = items["guide/budget.md#max_steps"]
    assert budget["retrieval"]["hit_top3"] is True  # ai_harness/guide/budget.md が末尾一致
    assert budget["deterministic"]["cited"] is True
    assert doc["metrics"]["retrieval_hit_rate_top3"] is not None
    # プロンプトに資料が差し込まれている
    answer_reqs = [r for r in router.chat_requests if r["model"] == ANSWER_MODEL]
    assert any("以下の資料だけを根拠に答え" in r["messages"][0]["content"] for r in answer_reqs)


@respx.mock(base_url=BASE_URL, assert_all_called=False)
def test_cli_refuses_same_grader_and_answer_model(respx_mock, router: FakeRouter, dataset_path: Path, tmp_path: Path, monkeypatch):
    """EVAL-003 / GRADER_SEPARATION: 同じモデルなら何も実行せず終了コード 2。"""
    mount_router(respx_mock, router)
    rc = _run_cli(
        ["--config", "A", "--dataset", str(dataset_path), "--base-url", BASE_URL,
         "--large-model", ANSWER_MODEL, "--grader-model", ANSWER_MODEL, "--out-dir", str(tmp_path / "r")],
        monkeypatch,
    )
    assert rc == 2
    assert router.calls == [] and router.chat_requests == []
    assert not (tmp_path / "r").exists()


def test_schema_rejects_same_models_marker():
    """Schema は differs_from_answer_model: false を拒否する。"""
    doc = {"grader": {"differs_from_answer_model": False}}
    errors = [e for e in VALIDATOR.iter_errors(doc) if list(e.path) == ["grader", "differs_from_answer_model"]]
    assert errors and "True" in errors[0].message


def test_check_eval_results_script_detects_same_model(tmp_path: Path):
    sys.path.insert(0, str(ROOT / "scripts" / "agent"))
    import check_eval_results  # noqa: E402

    good = json.loads((ROOT / "tests" / "eval" / "fixtures" / "result-ok.json").read_text(encoding="utf-8"))
    bad = json.loads(json.dumps(good))
    bad["grader"]["model"] = bad["answer"]["model"]
    good_path, bad_path = tmp_path / "20260101-000000-qa-A.json", tmp_path / "20260101-000001-qa-A.json"
    good_path.write_text(json.dumps(good, ensure_ascii=False), encoding="utf-8")
    bad_path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    assert check_eval_results.main([str(good_path)]) == 0
    assert check_eval_results.main([str(bad_path)]) == 1  # const true は保っているが文字列比較で落ちる


@respx.mock(base_url=BASE_URL, assert_all_called=False)
async def test_runner_without_llm_grade_and_without_model_management(respx_mock, router: FakeRouter, dataset_path: Path, rag_db: Path):
    mount_router(respx_mock, router)
    router.status[ANSWER_MODEL] = "loaded"
    cfg = EvalConfig("E", "test", ANSWER_MODEL, True, "test", top_k=1)
    async with LlamaClient(BASE_URL) as client:
        retriever = Retriever(RagStore(rag_db), Embedder(client, EMBED_MODEL))
        runner = EvalRunner(client, cfg, grader_model=GRADER_MODEL, retriever=retriever,
                            options=RunOptions(n=2, seed=1, llm_grade=False, manage_models=False))
        result = await runner.run(load_dataset(dataset_path))
    assert validate(result) == []
    assert router.calls == []
    assert result["dataset"]["n"] == 2 and len(result["dataset"]["indices"]) == 2
    assert all(it["llm"] is None for it in result["items"])
    assert result["metrics"]["llm_mean_score"] is None and result["grader"]["llm_grading_enabled"] is False


def test_runner_requires_retriever_for_rag_and_rejects_same_model():
    cfg = EvalConfig("B", "x", ANSWER_MODEL, True, "x")
    with pytest.raises(GraderSeparationError):
        EvalRunner(LlamaClient(BASE_URL), cfg, grader_model=ANSWER_MODEL, retriever=None)
    with pytest.raises(ValueError):
        EvalRunner(LlamaClient(BASE_URL), cfg, grader_model=GRADER_MODEL, retriever=None)


def test_summarize_picks_latest_per_config(tmp_path: Path, capsys):
    fixture = json.loads((ROOT / "tests" / "eval" / "fixtures" / "result-ok.json").read_text(encoding="utf-8"))
    for stamp, cid, correct in [("20260101-000000", "A", 1), ("20260102-000000", "A", 2), ("20260101-000000", "C", 3)]:
        doc = json.loads(json.dumps(fixture))
        doc["config"]["id"] = cid
        doc["metrics"]["deterministic_correct"] = correct
        (tmp_path / f"{stamp}-qa-{cid}.json").write_text(json.dumps(doc), encoding="utf-8")
    paths = latest_per_config(tmp_path, "qa")
    assert [p.name for p in paths] == ["20260102-000000-qa-A.json", "20260101-000000-qa-C.json"]
    table = render_comparison(load_results(paths))
    assert table.count("\n") == 4 and "| A |" in table and "| C |" in table and "2 / 3" in table
    assert cli.main(["--summarize", "--out-dir", str(tmp_path)]) == 0
    assert "| 構成 |" in capsys.readouterr().out
    assert cli.main(["--summarize", "--out-dir", str(tmp_path / "empty")]) == 1
