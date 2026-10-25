"""criterion ごとの決定論的な検証。

契約の completion_criteria[].criterion_id をそのまま検査名として使う(CHECKS に登録された ID だけ判定でき、
未登録の ID は not_run)。入力は evidence/<run_id>/events.jsonl と result.json だけで、
エージェントの本文(「できました」)は見ない(NO_SELF_REPORT)。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .contract import Contract
from .scope import check_read_scope

if TYPE_CHECKING:  # pragma: no cover
    from .runner import RunOutcome

CORPUS_DIR = Path("data") / "corpus"


def load_events(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


@dataclass
class VerifyContext:
    contract: Contract
    events: list[dict]
    workspace: Path
    evidence_dir: Path
    run: RunOutcome | None = None

    @property
    def result(self) -> dict | None:
        p = self.evidence_dir / "result.json"
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def of_type(self, *types: str) -> list[dict]:
        return [e for e in self.events if e.get("type") in types]

    @property
    def stop_reason(self) -> str | None:
        """events.jsonl から停止理由を取る。finished は停止ではないので None 扱いにしない(そのまま返す)。"""
        for e in reversed(self.events):
            if e.get("type") == "run_end":
                return e.get("stop_reason")
        stops = self.of_type("stop")
        return stops[-1].get("stop_reason") if stops else None

    @property
    def abnormal_stop(self) -> str | None:
        reason = self.stop_reason
        return reason if reason not in (None, "finished") else None


@dataclass
class CriterionOutcome:
    criterion_id: str
    status: str  # pass / fail / not_run / blocked
    notes: str
    evidence_refs: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    verifier: str = "local_llm.agent.verify"


Check = Callable[[VerifyContext], CriterionOutcome]
CHECKS: dict[str, Check] = {}


def check(criterion_id: str) -> Callable[[Check], Check]:
    def deco(fn: Check) -> Check:
        CHECKS[criterion_id] = fn
        return fn
    return deco


def _ev(ctx: VerifyContext, *names: str) -> list[str]:
    return [str((ctx.evidence_dir / n).as_posix()) for n in names]


@check("FINISH_CALLED")
def finish_called(ctx: VerifyContext) -> CriterionOutcome:
    finishes = ctx.of_type("finish")
    result = ctx.result
    if finishes and result is not None:
        return CriterionOutcome("FINISH_CALLED", "pass", f"finish が {len(finishes)} 回呼ばれ result.json がある",
                                _ev(ctx, "events.jsonl", "result.json"))
    why = "finish が呼ばれていない" if not finishes else "result.json が無いか壊れている"
    if ctx.abnormal_stop:
        why += f"(停止理由: {ctx.stop_reason})"
    return CriterionOutcome("FINISH_CALLED", "fail", why, _ev(ctx, "events.jsonl"))


@check("SUMMARY_NONEMPTY")
def summary_nonempty(ctx: VerifyContext) -> CriterionOutcome:
    result = ctx.result or {}
    summary = result.get("summary")
    if isinstance(summary, str) and summary.strip():
        return CriterionOutcome("SUMMARY_NONEMPTY", "pass", f"summary {len(summary)} 文字", _ev(ctx, "result.json"))
    return CriterionOutcome("SUMMARY_NONEMPTY", "fail", "result.summary が空、または result.json が無い", _ev(ctx, "result.json"))


@check("SOURCES_IN_SCOPE")
def sources_in_scope(ctx: VerifyContext) -> CriterionOutcome:
    result = ctx.result or {}
    sources = result.get("sources")
    if not isinstance(sources, list) or not sources:
        return CriterionOutcome("SOURCES_IN_SCOPE", "fail", "result.sources が空", _ev(ctx, "result.json"))
    scope = ctx.contract.scope
    bad: list[str] = []
    for src in sources:
        if not isinstance(src, str):
            bad.append(f"{src!r}: 文字列でない")
            continue
        candidates = [src, str(CORPUS_DIR / src)]
        ok = False
        reasons = []
        for cand in candidates:
            reason = check_read_scope(ctx.workspace, cand, scope)
            exists = (ctx.workspace / cand).is_file()
            if reason is None and exists:
                ok = True
                break
            reasons.append(reason or ("存在しない" if not exists else ""))
        if not ok:
            bad.append(f"{src}: {reasons[0]}")
    if bad:
        return CriterionOutcome("SOURCES_IN_SCOPE", "fail", f"{len(bad)}/{len(sources)} 件が不正: " + "; ".join(bad[:3]),
                                _ev(ctx, "result.json"))
    return CriterionOutcome("SOURCES_IN_SCOPE", "pass", f"{len(sources)} 件すべて scope.read 内に実在", _ev(ctx, "result.json"))


@check("ONLY_ALLOWED_TOOLS")
def only_allowed_tools(ctx: VerifyContext) -> CriterionOutcome:
    allowed = set(ctx.contract.allowed_tools)
    executed = [e for e in ctx.of_type("tool_call") if e.get("tool") not in allowed]
    blocked = [e for e in ctx.of_type("block") if e.get("code") == "tool_not_allowed"]
    note = f"実行されたツールはすべて許可内(許可外の呼び出し {len(blocked)} 件は block)"
    if executed:
        return CriterionOutcome("ONLY_ALLOWED_TOOLS", "fail",
                                "許可外ツールが実行された: " + ", ".join(sorted({e['tool'] for e in executed})), _ev(ctx, "events.jsonl"))
    return CriterionOutcome("ONLY_ALLOWED_TOOLS", "pass", note, _ev(ctx, "events.jsonl"))


@check("NO_SCOPE_VIOLATION")
def no_scope_violation(ctx: VerifyContext) -> CriterionOutcome:
    violations = [e for e in ctx.of_type("block") if e.get("code") == "scope_boundary_exceeded"]
    if violations:
        first = violations[0]
        return CriterionOutcome("NO_SCOPE_VIOLATION", "fail",
                                f"scope 外アクセスが {len(violations)} 件(例: {first.get('tool')} {first.get('reason')})",
                                _ev(ctx, "events.jsonl"))
    return CriterionOutcome("NO_SCOPE_VIOLATION", "pass", "scope 外アクセスなし(block 0 件)", _ev(ctx, "events.jsonl"))


@check("WITHIN_BUDGET")
def within_budget(ctx: VerifyContext) -> CriterionOutcome:
    reason = ctx.abnormal_stop
    run_end = (ctx.of_type("run_end") or [{}])[-1]
    counts = f"steps={run_end.get('steps')}, tool_calls={run_end.get('tool_calls')}, blocks={run_end.get('blocks')}"
    if reason:
        return CriterionOutcome("WITHIN_BUDGET", "fail", f"stop_reason={reason}({counts})", _ev(ctx, "events.jsonl"))
    if not run_end:
        return CriterionOutcome("WITHIN_BUDGET", "fail", "run_end レコードが無い(pi が正常終了していない)", _ev(ctx, "events.jsonl"))
    return CriterionOutcome("WITHIN_BUDGET", "pass", f"予算内で終了({counts})", _ev(ctx, "events.jsonl"))


@check("RUN_SETTLED")
def run_settled(ctx: VerifyContext) -> CriterionOutcome:
    run = ctx.run
    if run is None:
        return CriterionOutcome("RUN_SETTLED", "not_run", "runner の実行情報が無い")
    if run.settled and not run.error:
        return CriterionOutcome("RUN_SETTLED", "pass", f"agent_settled 受信、{run.duration_s}s、exit={run.exit_code}",
                                _ev(ctx, "pi_events.jsonl"))
    return CriterionOutcome("RUN_SETTLED", "fail", run.error or "agent_settled が来なかった", _ev(ctx, "pi_events.jsonl"))


def verify_all(ctx: VerifyContext) -> list[CriterionOutcome]:
    outcomes = []
    for crit in ctx.contract.criteria:
        cid = crit["criterion_id"]
        fn = CHECKS.get(cid)
        if fn is None:
            outcomes.append(CriterionOutcome(cid, "not_run", "この criterion_id に対応する決定論的な検査が runner に無い",
                                             limitations=["人のレビューが必要"]))
            continue
        outcomes.append(fn(ctx))
    return outcomes
