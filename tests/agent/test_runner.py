"""runner.py: 偽の pi(tests/agent/fake_pi.py)で RPC の流れと run → verify → report を通す(AGT-004 integration-test)。"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from local_llm.agent.runner import RunConfig, PiRunner, execute

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "agent" / "contracts" / "summarize-docs.json"
FAKE_PI = ROOT / "tests" / "agent" / "fake_pi.py"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """契約と Schema を持つ最小のワークスペース。README.md を根拠ファイルにする。"""
    (tmp_path / "agent" / "contracts").mkdir(parents=True)
    shutil.copy(SAMPLE, tmp_path / "agent" / "contracts" / "summarize-docs.json")
    (tmp_path / "docs" / "harness").mkdir(parents=True)
    shutil.copytree(ROOT / "docs" / "harness" / "schemas", tmp_path / "docs" / "harness" / "schemas")
    (tmp_path / "README.md").write_text("# readme", encoding="utf-8")
    return tmp_path


def config(workspace: Path, scenario: str, monkeypatch: pytest.MonkeyPatch, **kw) -> RunConfig:
    monkeypatch.setenv("FAKE_PI_SCENARIO", scenario)
    return RunConfig(contract_path=workspace / "agent" / "contracts" / "summarize-docs.json", task="RAG の設計方針を 3 点にまとめて",
                     workspace=workspace, run_id=f"test-{scenario}", pi_command=[sys.executable, str(FAKE_PI)], timeout=60, **kw)


def test_build_command_and_env(workspace: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = config(workspace, "finish", monkeypatch, budget_overrides={"max_steps": 2})
    runner = PiRunner(cfg)
    cmd = runner.build_command()
    assert cmd[:2] == [sys.executable, str(FAKE_PI)]
    for flag in ("--mode", "rpc", "--no-session", "--no-builtin-tools", "--no-extensions", "-e", "--model", "--system-prompt"):
        assert flag in cmd
    assert cmd[cmd.index("-e") + 1].endswith(str(Path("agent") / "harness" / "index.ts"))
    env = runner.build_env()
    assert env["PI_CODING_AGENT_DIR"] == str(workspace / "agent" / "pi")
    assert env["HARNESS_CONTRACT"].endswith("summarize-docs.json")
    assert env["HARNESS_EVIDENCE_DIR"] == str(workspace / "evidence" / "test-finish")
    assert json.loads(env["HARNESS_BUDGET"]) == {"max_steps": 2}
    assert env["HARNESS_THINKING"] == "off"
    cfg2 = config(workspace, "finish", monkeypatch, expose_builtin_tools=True)
    assert "--no-builtin-tools" not in PiRunner(cfg2).build_command()


def test_execute_accepted_with_fake_pi(workspace: Path, monkeypatch: pytest.MonkeyPatch):
    outcome, decision, paths = execute(config(workspace, "finish", monkeypatch))
    assert outcome.settled and outcome.error is None and outcome.exit_code == 0
    assert outcome.assistant_text == "done"
    assert decision["status"] == "accepted"
    by_id = {c["criterion_id"]: c["status"] for c in decision["criterion_results"]}
    assert by_id["FINISH_CALLED"] == "pass" and by_id["SOURCES_IN_SCOPE"] == "pass" and by_id["RUN_SETTLED"] == "pass"
    assert by_id["SUMMARY_QUALITY"] == "not_run"
    ev = workspace / "evidence" / "test-finish"
    for name in ("events.jsonl", "result.json", "pi_events.jsonl", "decision.json", "verification.json", "report.md"):
        assert (ev / name).exists(), name
    assert paths["decision"] == ev / "decision.json"
    pi_events = [json.loads(l) for l in (ev / "pi_events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert pi_events[-1]["type"] == "agent_settled"


def test_execute_budget_exceeded_is_blocked(workspace: Path, monkeypatch: pytest.MonkeyPatch):
    outcome, decision, _ = execute(config(workspace, "budget", monkeypatch, budget_overrides={"max_steps": 2}))
    assert outcome.settled
    assert outcome.error is None and outcome.aborted_turns == 1  # ハーネス起因の abort は runner エラーではない
    assert decision["status"] == "blocked"
    assert any(e["code"] == "budget_exceeded" for e in decision["unresolved_errors"])
    by_id = {c["criterion_id"]: c for c in decision["criterion_results"]}
    assert by_id["WITHIN_BUDGET"]["status"] == "fail" and "budget_exceeded" in by_id["WITHIN_BUDGET"]["notes"]
    assert by_id["RUN_SETTLED"]["status"] == "pass"


def test_execute_blocked_bash_recorded(workspace: Path, monkeypatch: pytest.MonkeyPatch):
    _, decision, _ = execute(config(workspace, "bash", monkeypatch))
    events = [json.loads(l) for l in (workspace / "evidence" / "test-bash" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    blocks = [e for e in events if e["type"] == "block"]
    assert blocks and blocks[0]["tool"] == "bash" and blocks[0]["code"] == "tool_not_allowed"
    assert decision["status"] == "accepted"  # block は記録されるが、許可外ツールは実行されていないので採用できる


def test_execute_rejects_bad_contract_hash(workspace: Path, monkeypatch: pytest.MonkeyPatch):
    p = workspace / "agent" / "contracts" / "summarize-docs.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    data["objective"] = "changed"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SystemExit, match="contract_hash"):
        execute(config(workspace, "finish", monkeypatch))


def test_missing_pi_command_is_reported(workspace: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = config(workspace, "finish", monkeypatch)
    cfg.pi_command = ["definitely-not-a-command-xyz"]
    outcome = PiRunner(cfg).run()
    assert outcome.error and "起動できません" in outcome.error
    assert not outcome.settled
