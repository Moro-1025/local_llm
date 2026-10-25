"""verify.py / report.py: 合成した events.jsonl から criterion の合否と decision の status が決まる。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from local_llm.agent.contract import load_contract
from local_llm.agent.report import decide_status, write_outputs
from local_llm.agent.verify import CriterionOutcome, VerifyContext, verify_all

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "agent" / "contracts" / "summarize-docs.json"
SCHEMA_DIR = ROOT / "docs" / "harness" / "schemas"


def write_events(evidence_dir: Path, events: list[dict], result: dict | None = None) -> None:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    with (evidence_dir / "events.jsonl").open("w", encoding="utf-8") as f:
        for i, e in enumerate(events, 1):
            f.write(json.dumps({"seq": i, "ts": "2026-10-25T00:00:00Z", "run_id": evidence_dir.name,
                                "contract_hash": "sha256:" + "0" * 64, **e}, ensure_ascii=False) + "\n")
    if result is not None:
        (evidence_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "spec.md").write_text("# spec", encoding="utf-8")
    (tmp_path / "README.md").write_text("# readme", encoding="utf-8")
    (tmp_path / "data" / "corpus" / "ai_harness").mkdir(parents=True)
    (tmp_path / "data" / "corpus" / "ai_harness" / "README.md").write_text("# corpus", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "x.py").write_text("x = 1", encoding="utf-8")
    return tmp_path


def make_ctx(workspace: Path, events: list[dict], result: dict | None, run=None) -> VerifyContext:
    ev = workspace / "evidence" / "run-1"
    write_events(ev, events, result)
    from local_llm.agent.verify import load_events
    return VerifyContext(contract=load_contract(SAMPLE), events=load_events(ev / "events.jsonl"), workspace=workspace,
                         evidence_dir=ev, run=run)


GOOD_EVENTS = [
    {"type": "run_start"},
    {"type": "tool_call", "tool": "rag_search", "args": {"query": "q"}},
    {"type": "tool_result", "tool": "rag_search", "is_error": False, "result_preview": "x"},
    {"type": "tool_call", "tool": "finish", "args": {}},
    {"type": "finish", "sources": ["docs/spec.md", "ai_harness/README.md"]},
    {"type": "run_end", "steps": 2, "tool_calls": 2, "blocks": 0, "finished": True, "stop_reason": "finished"},
]


def test_all_pass_when_finish_and_sources_ok(workspace: Path):
    from local_llm.agent.runner import RunOutcome

    run = RunOutcome("run-1", workspace / "evidence" / "run-1", Path("pi.jsonl"), 0, 1.0, True)
    ctx = make_ctx(workspace, GOOD_EVENTS, {"summary": "三点", "sources": ["docs/spec.md", "ai_harness/README.md"]}, run)
    outcomes = {o.criterion_id: o for o in verify_all(ctx)}
    for cid in ("FINISH_CALLED", "SUMMARY_NONEMPTY", "SOURCES_IN_SCOPE", "ONLY_ALLOWED_TOOLS", "NO_SCOPE_VIOLATION",
                "WITHIN_BUDGET", "RUN_SETTLED"):
        assert outcomes[cid].status == "pass", (cid, outcomes[cid].notes)
    assert outcomes["SUMMARY_QUALITY"].status == "not_run"  # human-review: runner は判定しない
    decision, paths = write_outputs(ctx, list(outcomes.values()), task="t", schema_dir=SCHEMA_DIR)
    assert decision["status"] == "accepted"
    assert decision["required_criteria_complete"] is True
    assert not any(e["code"] == "SCHEMA_ERRORS" for e in decision["unresolved_errors"])
    assert paths["report"].exists() and "**accepted**" in paths["report"].read_text(encoding="utf-8")
    ver = json.loads(paths["verification"].read_text(encoding="utf-8"))
    assert {v["criterion_id"] for v in ver} == set(outcomes)


def test_sources_outside_scope_or_missing_fail(workspace: Path):
    ctx = make_ctx(workspace, GOOD_EVENTS, {"summary": "s", "sources": ["src/x.py"]})
    out = {o.criterion_id: o for o in verify_all(ctx)}
    assert out["SOURCES_IN_SCOPE"].status == "fail" and "scope.read" in out["SOURCES_IN_SCOPE"].notes
    ctx2 = make_ctx(workspace, GOOD_EVENTS, {"summary": "s", "sources": ["docs/none.md"]})
    assert {o.criterion_id: o for o in verify_all(ctx2)}["SOURCES_IN_SCOPE"].status == "fail"
    ctx3 = make_ctx(workspace, GOOD_EVENTS, {"summary": "s", "sources": []})
    assert {o.criterion_id: o for o in verify_all(ctx3)}["SOURCES_IN_SCOPE"].status == "fail"
    decision, _ = write_outputs(ctx3, verify_all(ctx3), task="t", schema_dir=SCHEMA_DIR)
    assert decision["status"] == "rejected"


def test_budget_exceeded_is_blocked_with_stop_reason(workspace: Path):
    events = [
        {"type": "run_start"},
        {"type": "tool_call", "tool": "rag_search", "args": {}},
        {"type": "stop", "stop_reason": "budget_exceeded", "code": "budget_exceeded", "reason": "max_steps=2 exceeded"},
        {"type": "block", "tool": "read_file", "code": "run_stopped", "reason": "run already stopped", "stop_reason": "budget_exceeded"},
        {"type": "run_end", "steps": 3, "tool_calls": 2, "blocks": 1, "finished": False, "stop_reason": "budget_exceeded"},
    ]
    ctx = make_ctx(workspace, events, None)
    out = {o.criterion_id: o for o in verify_all(ctx)}
    assert out["WITHIN_BUDGET"].status == "fail" and "budget_exceeded" in out["WITHIN_BUDGET"].notes
    assert out["FINISH_CALLED"].status == "fail"
    decision, paths = write_outputs(ctx, list(out.values()), task="t", schema_dir=SCHEMA_DIR)
    assert decision["status"] == "blocked"
    assert any(e["code"] == "budget_exceeded" for e in decision["unresolved_errors"])
    assert any("stop_reason: budget_exceeded" in r for r in decision["reasons"])
    assert "budget_exceeded" in paths["report"].read_text(encoding="utf-8")


def test_blocked_bash_is_recorded_but_run_can_still_pass(workspace: Path):
    events = [
        {"type": "run_start"},
        {"type": "block", "tool": "bash", "args": {"command": "ls"}, "code": "tool_not_allowed", "reason": "not allowed"},
        *GOOD_EVENTS[1:],
    ]
    ctx = make_ctx(workspace, events, {"summary": "s", "sources": ["README.md"]})
    out = {o.criterion_id: o for o in verify_all(ctx)}
    assert out["ONLY_ALLOWED_TOOLS"].status == "pass" and "1 件は block" in out["ONLY_ALLOWED_TOOLS"].notes
    # 許可外ツールが「実行」されたら fail
    bad = [{"type": "tool_call", "tool": "bash", "args": {}}, *GOOD_EVENTS[1:]]
    out2 = {o.criterion_id: o for o in verify_all(make_ctx(workspace, bad, {"summary": "s", "sources": ["README.md"]}))}
    assert out2["ONLY_ALLOWED_TOOLS"].status == "fail"


def test_scope_violation_blocks_decision(workspace: Path):
    events = [
        {"type": "run_start"},
        {"type": "block", "tool": "read_file", "args": {"path": "../x"}, "code": "scope_boundary_exceeded", "reason": "escapes",
         "stop_reason": "scope_violation"},
        {"type": "run_end", "steps": 1, "tool_calls": 1, "blocks": 1, "finished": False, "stop_reason": "scope_violation"},
    ]
    ctx = make_ctx(workspace, events, None)
    out = {o.criterion_id: o for o in verify_all(ctx)}
    assert out["NO_SCOPE_VIOLATION"].status == "fail"
    decision, _ = write_outputs(ctx, list(out.values()), task="t", schema_dir=SCHEMA_DIR)
    assert decision["status"] == "blocked" and decision["scope_check"] == "fail"


def test_decide_status_rules():
    crit = [{"criterion_id": "A", "required": True}, {"criterion_id": "B", "required": False}]
    ok = [CriterionOutcome("A", "pass", ""), CriterionOutcome("B", "fail", "")]
    assert decide_status(crit, ok, None) == "accepted"  # 任意 criterion の fail は accepted を妨げない
    assert decide_status(crit, [CriterionOutcome("A", "fail", "")], None) == "rejected"
    assert decide_status(crit, [CriterionOutcome("A", "not_run", "")], None) == "needs_human"
    assert decide_status(crit, ok, "budget_exceeded") == "blocked"
