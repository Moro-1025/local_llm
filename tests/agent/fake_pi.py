"""pi --mode rpc の偽物。runner のテストで LLM と Node 無しに RPC の流れを再現する。

stdin から JSON 1 行ずつ読み、prompt が来たらシナリオどおりのイベントを stdout に書く。
本物では extension が書く evidence/<run_id>/events.jsonl と result.json も、ここでは偽物が書く
(HARNESS_EVIDENCE_DIR を見る)。シナリオは環境変数 FAKE_PI_SCENARIO で選ぶ:

  finish   rag_search → read_file → finish。result.json あり。予算内で終了
  budget   max_steps=2 を超えて停止(block budget_exceeded、stop、run_end stop_reason=budget_exceeded)
  bash     許可外ツール bash が block され、その後 finish
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path


def out(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


class Evidence:
    def __init__(self) -> None:
        self.dir = Path(os.environ["HARNESS_EVIDENCE_DIR"])
        self.dir.mkdir(parents=True, exist_ok=True)
        self.run_id = os.environ.get("HARNESS_RUN_ID", "fake")
        self.hash = "sha256:" + "f" * 64
        self.seq = 0
        self.path = self.dir / "events.jsonl"

    def append(self, type_: str, **data: object) -> None:
        self.seq += 1
        rec = {"seq": self.seq, "ts": datetime.now(UTC).isoformat(), "run_id": self.run_id,
               "contract_hash": self.hash, "type": type_, **data}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def scenario_finish(ev: Evidence) -> None:
    ev.append("run_start", allowed_tools=["rag_search", "read_file", "list_files", "finish"])
    ev.append("turn_start", turn_index=0, step=1)
    ev.append("tool_call", tool="rag_search", args={"query": "RAG"}, args_hash="sha256:" + "1" * 64)
    ev.append("tool_result", tool="rag_search", is_error=False, result_preview="[1] docs/spec.md › RAG", result_chars=30)
    ev.append("turn_start", turn_index=1, step=2)
    ev.append("tool_call", tool="read_file", args={"path": "README.md"}, args_hash="sha256:" + "2" * 64)
    ev.append("tool_result", tool="read_file", is_error=False, result_preview="# local_llm", result_chars=11)
    ev.append("turn_start", turn_index=2, step=3)
    (ev.dir / "result.json").write_text(json.dumps({"summary": "要点 1\n要点 2\n要点 3", "sources": ["README.md"]},
                                                   ensure_ascii=False, indent=2), encoding="utf-8")
    ev.append("tool_call", tool="finish", args={"summary": "...", "sources": ["README.md"]}, args_hash="sha256:" + "3" * 64)
    ev.append("finish", summary_chars=15, sources=["README.md"])
    ev.append("tool_result", tool="finish", is_error=False, result_preview="result recorded", result_chars=15)
    ev.append("run_end", steps=3, tool_calls=3, blocks=0, finished=True, stop_reason="finished")
    out({"type": "tool_execution_end", "toolCallId": "c1", "toolName": "finish", "isError": False})
    out({"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": "done"}]}})


def scenario_budget(ev: Evidence) -> None:
    ev.append("run_start", budget={"max_steps": 2})
    ev.append("turn_start", turn_index=0, step=1)
    ev.append("tool_call", tool="rag_search", args={"query": "RAG"}, args_hash="sha256:" + "1" * 64)
    ev.append("tool_result", tool="rag_search", is_error=False, result_preview="...", result_chars=3)
    ev.append("turn_start", turn_index=1, step=2)
    ev.append("tool_call", tool="read_file", args={"path": "README.md"}, args_hash="sha256:" + "2" * 64)
    ev.append("tool_result", tool="read_file", is_error=False, result_preview="...", result_chars=3)
    ev.append("turn_start", turn_index=2, step=3)
    ev.append("stop", stop_reason="budget_exceeded", code="budget_exceeded", reason="max_steps=2 exceeded (step 3)")
    ev.append("block", tool="read_file", args={"path": "docs/a.md"}, code="run_stopped", reason="run already stopped: budget_exceeded",
              stop_reason="budget_exceeded")
    ev.append("settle", **{"continue": False, "stop_reason": "budget_exceeded"})
    ev.append("run_end", steps=3, tool_calls=3, blocks=1, finished=False, stop_reason="budget_exceeded")
    # 本物の pi 1.0 は turn_start で ctx.abort() された turn を stopReason=error + errorMessage 付きで返す
    out({"type": "message_end", "message": {"role": "assistant", "content": [], "stopReason": "error",
                                            "errorMessage": "This operation was aborted"}})


def scenario_bash(ev: Evidence) -> None:
    ev.append("run_start")
    ev.append("turn_start", turn_index=0, step=1)
    ev.append("block", tool="bash", args={"command": "ls"}, code="tool_not_allowed",
              reason="tool 'bash' is not in capability_profile.allowed_tools", stop_reason=None)
    ev.append("turn_start", turn_index=1, step=2)
    (ev.dir / "result.json").write_text(json.dumps({"summary": "ok", "sources": ["README.md"]}), encoding="utf-8")
    ev.append("tool_call", tool="finish", args={"summary": "ok", "sources": ["README.md"]}, args_hash="sha256:" + "3" * 64)
    ev.append("finish", summary_chars=2, sources=["README.md"])
    ev.append("run_end", steps=2, tool_calls=2, blocks=1, finished=True, stop_reason="finished")


SCENARIOS = {"finish": scenario_finish, "budget": scenario_budget, "bash": scenario_bash}


def main() -> None:
    scenario = SCENARIOS[os.environ.get("FAKE_PI_SCENARIO", "finish")]
    ev = Evidence()
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        cmd = json.loads(raw)
        if cmd.get("type") == "prompt":
            out({"id": cmd.get("id"), "type": "response", "command": "prompt", "success": True, "data": {"disposition": "started"}})
            out({"type": "agent_start"})
            out({"type": "turn_start", "turnIndex": 0})
            scenario(ev)
            out({"type": "turn_end"})
            out({"type": "agent_end", "messages": [], "willRetry": False})
            out({"type": "agent_settled"})
        elif cmd.get("type") == "abort":
            out({"type": "response", "command": "abort", "success": True})
        else:
            out({"id": cmd.get("id"), "type": "response", "command": cmd.get("type"), "success": True, "data": {}})
    # stdin が閉じたら終了(本物の pi と同じ)


if __name__ == "__main__":
    main()
