"""pi --mode rpc をハーネス extension 付きで起動し、1 つのタスクを実行する。

    uv run llm-agent --contract agent/contracts/summarize-docs.json --task "RAG の設計方針を 3 点にまとめて"

流れ: 契約を読む → evidence/<run_id>/ を作る → pi を子プロセスで起動(stdin に JSON 1 行、stdout から JSON 1 行ずつ)
→ prompt を送る → agent_settled を待つ → stdin を閉じて終了を待つ → verify.py で criterion ごとに判定
→ report.py で decision.json / verification.json / report.md を書く。

エージェントの「できました」(finish の内容)はそのまま採用しない。判定は runner の検証結果だけで決める。
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import secrets
import shlex
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .contract import SCHEMA_RELATIVE, Contract, load_contract
from .report import write_outputs
from .verify import VerifyContext, load_events, verify_all

DEFAULT_MODEL = "llama-local/unsloth/Qwen3.6-35B-A3B-GGUF:Q4_K_M"
DEFAULT_RAG_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT = 900

DEFAULT_SYSTEM_PROMPT = """You are a read-only documentation agent running inside a harness.
Available tools: rag_search(query, top_k), read_file(path), list_files(glob), finish(summary, sources).
Workflow:
1. Call rag_search once with the user's question (top_k 3) to find relevant document chunks.
2. Optionally read_file at most two of the most relevant files (paths from rag_search are relative to data/corpus/; project docs are under docs/).
3. Call finish exactly once with `summary` (answer in the user's language, Markdown allowed) and `sources` (the file paths you relied on).
Rules: the context window is small (8k tokens) and the total tool output is capped by the harness, so keep tool calls few,
do not repeat a search, and call finish as soon as you have enough material. You have no shell and cannot write files.
Do not ask the user questions. Base the answer only on what the tools returned.
Only the finish call counts as your answer; the harness verifies it."""


def new_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


@dataclass
class RunConfig:
    contract_path: Path
    task: str
    workspace: Path = field(default_factory=lambda: Path.cwd())
    run_id: str = field(default_factory=new_run_id)
    evidence_root: Path = Path("evidence")
    rag_url: str = DEFAULT_RAG_URL
    pi_command: list[str] = field(default_factory=lambda: ["pi"])
    model: str = DEFAULT_MODEL
    thinking: str = "off"
    timeout: float = DEFAULT_TIMEOUT
    budget_overrides: dict | None = None
    expose_builtin_tools: bool = False
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    extension_path: Path | None = None
    agent_dir: Path | None = None

    @property
    def evidence_dir(self) -> Path:
        root = self.evidence_root if self.evidence_root.is_absolute() else self.workspace / self.evidence_root
        return root / self.run_id


@dataclass
class RunOutcome:
    run_id: str
    evidence_dir: Path
    pi_events_path: Path
    exit_code: int | None
    duration_s: float
    settled: bool
    error: str | None = None
    assistant_text: str = ""
    turns: int = 0
    aborted_turns: int = 0  # ハーネスが ctx.abort() で打ち切った turn 数
    command: list[str] = field(default_factory=list)


class PiRunner:
    def __init__(self, config: RunConfig):
        self.config = config

    # --- 起動設定 -------------------------------------------------------
    def extension_path(self) -> Path:
        return self.config.extension_path or (self.config.workspace / "agent" / "harness" / "index.ts")

    def agent_dir(self) -> Path:
        return self.config.agent_dir or (self.config.workspace / "agent" / "pi")

    def build_command(self) -> list[str]:
        c = self.config
        cmd = list(c.pi_command)
        cmd += ["--mode", "rpc", "--no-session", "--no-extensions", "--no-skills", "--no-prompt-templates",
                "--no-context-files", "--no-themes"]
        if not c.expose_builtin_tools:
            cmd.append("--no-builtin-tools")
        cmd += ["-e", str(self.extension_path()), "--model", c.model, "--thinking", c.thinking,
                "--system-prompt", c.system_prompt]
        return cmd

    def build_env(self) -> dict[str, str]:
        c = self.config
        env = dict(os.environ)
        env["PI_CODING_AGENT_DIR"] = str(self.agent_dir())
        env["HARNESS_CONTRACT"] = str(Path(c.contract_path).resolve())
        env["HARNESS_RUN_ID"] = c.run_id
        env["HARNESS_EVIDENCE_DIR"] = str(c.evidence_dir)
        env["HARNESS_WORKSPACE"] = str(c.workspace.resolve())
        env["HARNESS_RAG_URL"] = c.rag_url
        env["HARNESS_THINKING"] = c.thinking
        if c.budget_overrides:
            env["HARNESS_BUDGET"] = json.dumps(c.budget_overrides)
        else:
            env.pop("HARNESS_BUDGET", None)
        return env

    # --- 実行 -------------------------------------------------------------
    def run(self) -> RunOutcome:
        c = self.config
        c.evidence_dir.mkdir(parents=True, exist_ok=True)
        pi_events_path = c.evidence_dir / "pi_events.jsonl"
        cmd = self.build_command()
        exe = shutil.which(cmd[0]) or cmd[0]
        cmd = [exe, *cmd[1:]]
        started = time.monotonic()
        try:
            proc = subprocess.Popen(
                cmd, cwd=str(c.workspace), env=self.build_env(),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
        except OSError as exc:
            return RunOutcome(c.run_id, c.evidence_dir, pi_events_path, None, 0.0, False,
                              error=f"pi を起動できません: {exc}", command=cmd)
        assert proc.stdin and proc.stdout and proc.stderr

        lines: queue.Queue[bytes | None] = queue.Queue()
        stderr_chunks: list[bytes] = []

        def pump_stdout() -> None:
            for raw in iter(proc.stdout.readline, b""):
                lines.put(raw)
            lines.put(None)

        def pump_stderr() -> None:
            stderr_chunks.append(proc.stderr.read())

        threading.Thread(target=pump_stdout, daemon=True).start()
        threading.Thread(target=pump_stderr, daemon=True).start()

        outcome = RunOutcome(c.run_id, c.evidence_dir, pi_events_path, None, 0.0, False, command=cmd)
        deadline = started + c.timeout
        with pi_events_path.open("w", encoding="utf-8") as sink:
            def send(obj: dict) -> None:
                proc.stdin.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
                proc.stdin.flush()

            send({"id": "prompt-1", "type": "prompt", "message": c.task})
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    outcome.error = f"timeout: {c.timeout:.0f} 秒以内に agent_settled が来ませんでした"
                    try:
                        send({"type": "abort"})
                    except OSError:
                        pass
                    break
                try:
                    raw = lines.get(timeout=min(remaining, 1.0))
                except queue.Empty:
                    if proc.poll() is not None:
                        outcome.error = f"pi が途中で終了しました(exit {proc.returncode})"
                        break
                    continue
                if raw is None:
                    if not outcome.settled:
                        outcome.error = outcome.error or f"pi の stdout が閉じました(exit {proc.poll()})"
                    break
                text = raw.decode("utf-8", "replace").rstrip("\r\n")
                if not text:
                    continue
                sink.write(text + "\n")
                try:
                    event = json.loads(text)
                except json.JSONDecodeError:
                    continue
                self._observe(event, outcome)
                if event.get("type") == "response" and event.get("success") is False:
                    outcome.error = f"pi コマンド失敗: {event.get('error')}"
                    break
                if event.get("type") == "agent_settled":
                    outcome.settled = True
                    break

        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
        outcome.exit_code = proc.returncode
        outcome.duration_s = round(time.monotonic() - started, 1)
        stderr_text = b"".join(stderr_chunks).decode("utf-8", "replace").strip()
        if stderr_text:
            (c.evidence_dir / "pi_stderr.txt").write_text(stderr_text + "\n", encoding="utf-8")
        return outcome

    @staticmethod
    def _observe(event: dict, outcome: RunOutcome) -> None:
        t = event.get("type")
        if t == "turn_start":
            outcome.turns += 1
        elif t == "message_end":
            msg = event.get("message") or {}
            if msg.get("role") == "assistant":
                parts = [c.get("text", "") for c in msg.get("content", []) if isinstance(c, dict) and c.get("type") == "text"]
                if any(parts):
                    outcome.assistant_text = "\n".join(p for p in parts if p)
                err = str(msg.get("errorMessage") or "")
                if msg.get("stopReason") == "aborted" or "aborted" in err.lower():
                    # ハーネス(拡張)が turn_start で ctx.abort() した turn。Pi 1.0 はこれを
                    # stopReason=error / errorMessage="This operation was aborted" として返すが、
                    # 止めたのはこちらなので runner のエラーではない(停止理由は events.jsonl の stop が持つ)。
                    outcome.aborted_turns += 1
                elif err:
                    outcome.error = outcome.error or f"model error: {err}"


# --- 一連の実行(run → verify → report) ------------------------------------
def execute(config: RunConfig) -> tuple[RunOutcome, dict, dict[str, Path]]:
    schema_dir = config.workspace / SCHEMA_RELATIVE
    contract: Contract = load_contract(config.contract_path, schema_dir=schema_dir)
    if contract.schema_errors:
        raise SystemExit("契約が Schema に適合しません:\n  " + "\n  ".join(contract.schema_errors))
    if not contract.hash_matches:
        raise SystemExit(
            f"contract_hash が内容と一致しません: {contract.data.get('contract_hash')} != {contract.hash}\n"
            "  uv run python scripts/agent/check_contracts.py --rehash で更新してください"
        )

    runner = PiRunner(config)
    outcome = runner.run()

    events = load_events(config.evidence_dir / "events.jsonl")
    ctx = VerifyContext(contract=contract, events=events, workspace=config.workspace, evidence_dir=config.evidence_dir,
                        run=outcome)
    results = verify_all(ctx)
    decision, paths = write_outputs(ctx, results, task=config.task, schema_dir=schema_dir)
    return outcome, decision, paths


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Pi をハーネス付きで 1 回実行し、decision.json と report.md を出す")
    p.add_argument("--contract", required=True, type=Path, help="Task Contract(JSON)")
    p.add_argument("--task", required=True, help="エージェントへの指示")
    p.add_argument("--run-id", default=None)
    p.add_argument("--evidence-dir", type=Path, default=Path("evidence"), help="証拠の置き場(既定 evidence/)")
    p.add_argument("--workspace", type=Path, default=Path.cwd())
    p.add_argument("--rag-url", default=os.environ.get("HARNESS_RAG_URL", DEFAULT_RAG_URL), help="llm-web の URL")
    p.add_argument("--model", default=os.environ.get("LLM_AGENT_MODEL", DEFAULT_MODEL), help="provider/model-id")
    p.add_argument("--thinking", default="off", help="pi --thinking の値")
    p.add_argument("--pi", default="pi", help="pi コマンド(テストでは偽の pi に差し替える)")
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="agent_settled を待つ秒数")
    p.add_argument("--max-steps", type=int, default=None, help="execution_budget.max_steps を上書き(AGT-003 の確認用)")
    p.add_argument("--max-tool-calls", type=int, default=None)
    p.add_argument("--expose-builtin-tools", action="store_true",
                   help="pi の組み込みツール(bash 等)を見せたまま実行する。許可外ツールの block を実機で確かめる用")
    args = p.parse_args(argv)

    overrides = {k: v for k, v in {"max_steps": args.max_steps, "max_tool_calls": args.max_tool_calls}.items() if v is not None}
    config = RunConfig(
        contract_path=args.contract, task=args.task, workspace=args.workspace.resolve(),
        run_id=args.run_id or new_run_id(), evidence_root=args.evidence_dir, rag_url=args.rag_url,
        pi_command=shlex.split(args.pi, posix=False) if " " in args.pi else [args.pi],
        model=args.model, thinking=args.thinking, timeout=args.timeout,
        budget_overrides=overrides or None, expose_builtin_tools=args.expose_builtin_tools,
    )
    outcome, decision, paths = execute(config)

    print(f"run_id: {outcome.run_id}  ({outcome.duration_s}s, turns={outcome.turns}, settled={outcome.settled}, exit={outcome.exit_code}{', aborted_turns=' + str(outcome.aborted_turns) if outcome.aborted_turns else ''})")
    if outcome.error:
        print(f"runner error: {outcome.error}")
    print(f"decision: {decision['status']}")
    for cr in decision["criterion_results"]:
        mark = {"pass": "PASS", "fail": "FAIL", "not_run": "----", "blocked": "BLCK"}.get(cr["status"], cr["status"])
        print(f"  {mark} {cr['criterion_id']:<22} {cr.get('notes') or ''}")
    for k, v in paths.items():
        print(f"{k}: {v}")
    sys.exit(0 if decision["status"] == "accepted" else 1)


if __name__ == "__main__":
    main()
