"""verification-result / completion-decision JSON と report.md を evidence/<run_id>/ に書く。

判定の規則(ai_harness の decision model に合わせる):
  - 停止理由(budget_exceeded / scope_violation / same_error_repeated)があれば blocked
  - 必須 criterion に fail があれば rejected
  - 必須 criterion に not_run / blocked があれば needs_human
  - 必須 criterion がすべて pass のときだけ accepted
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from .contract import validate_against_schema
from .verify import CriterionOutcome, VerifyContext

SCHEMA_VERSION = "1.0"


def sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def decide_status(contract_criteria: list[dict], outcomes: list[CriterionOutcome], abnormal_stop: str | None) -> str:
    required = {c["criterion_id"] for c in contract_criteria if c.get("required")}
    statuses = {o.criterion_id: o.status for o in outcomes}
    if abnormal_stop:
        return "blocked"
    if any(statuses.get(cid) == "fail" for cid in required):
        return "rejected"
    if any(statuses.get(cid) in (None, "not_run", "blocked") for cid in required):
        return "needs_human"
    return "accepted"


def build_outputs(ctx: VerifyContext, outcomes: list[CriterionOutcome], *, task: str, now: str | None = None) -> tuple[dict, list[dict], str]:
    contract = ctx.contract
    data = contract.data
    now = now or datetime.now(UTC).isoformat(timespec="seconds")
    run_id = ctx.evidence_dir.name
    events_path = ctx.evidence_dir / "events.jsonl"
    subject = {"type": "artifact", "artifact_ref": f"evidence/{run_id}/events.jsonl",
               "artifact_hash": sha256_file(events_path) or "sha256:" + "0" * 64}
    base = {
        "schema_version": SCHEMA_VERSION, "core_version": data.get("core_version", "1.0"),
        "task_id": data["task_id"], "contract_id": data["contract_id"],
        "contract_version": data["contract_version"], "contract_hash": data.get("contract_hash") or contract.hash,
    }
    crit_by_id = {c["criterion_id"]: c for c in contract.criteria}

    verifications = []
    for o in outcomes:
        verifications.append({
            **base,
            "result_id": f"vr:{data['task_id']}:{o.criterion_id}:{run_id}",
            "criterion_id": o.criterion_id,
            "assertion": crit_by_id.get(o.criterion_id, {}).get("assertion", ""),
            "status": o.status,
            "verifier": {"type": "integration-test" if o.status != "not_run" else "other", "name": o.verifier,
                         "version": None, "independent_from_writer": True},
            "subject": subject,
            "evidence_refs": o.evidence_refs,
            "policy_decision_refs": [], "approval_refs": [],
            "limitations": o.limitations,
            "verified_at": now,
        })

    abnormal = ctx.abnormal_stop
    status = decide_status(contract.criteria, outcomes, abnormal)
    required_complete = all(o.status == "pass" for o in outcomes if crit_by_id.get(o.criterion_id, {}).get("required"))
    unresolved = []
    if abnormal:
        unresolved.append({"severity": "high", "code": abnormal,
                           "description": f"ハーネスが停止条件で止めた: stop_reason={abnormal}",
                           "evidence_refs": [f"evidence/{run_id}/events.jsonl"]})
    scope_blocks = [e for e in ctx.of_type("block") if e.get("code") == "scope_boundary_exceeded"]
    tool_blocks = [e for e in ctx.of_type("block") if e.get("code") == "tool_not_allowed"]
    run = ctx.run
    if run is not None and run.error:
        unresolved.append({"severity": "medium", "code": "RUNNER_ERROR", "description": run.error,
                           "evidence_refs": [f"evidence/{run_id}/pi_events.jsonl"]})

    reasons = [f"task: {task}"]
    reasons.append(f"stop_reason: {ctx.stop_reason}" if ctx.stop_reason else "stop_reason: なし")
    reasons.append(f"block: tool_not_allowed {len(tool_blocks)} 件, scope_boundary_exceeded {len(scope_blocks)} 件")
    reasons.append("判定は runner の検証結果のみ(エージェントの finish 本文は採用の根拠にしない)")

    decision = {
        **base,
        "decision_id": f"cd:{data['task_id']}:{run_id}",
        "subject": subject,
        "status": status,
        "criterion_results": [
            {"criterion_id": o.criterion_id, "required": bool(crit_by_id.get(o.criterion_id, {}).get("required", False)),
             "status": o.status, "verification_result_refs": [f"evidence/{run_id}/verification.json#{o.criterion_id}"],
             "notes": o.notes}
            for o in outcomes
        ],
        "required_criteria_complete": required_complete,
        "evidence_binding_check": "pass" if events_path.exists() else "fail",
        "scope_check": "fail" if scope_blocks else "pass",
        "policy_check": "pass",
        "critical_unresolved_errors": 0,
        "unresolved_errors": unresolved,
        "uncertain_actions": [],
        "policy_decision_refs": [], "approval_refs": [],
        "residual_risks": [],
        "decided_by": {"type": "runtime", "identifier": "local_llm.agent.runner", "version": None},
        "decided_at": now,
        "reasons": reasons,
    }

    md = render_markdown(ctx, outcomes, decision, task=task, now=now)
    return decision, verifications, md


def render_markdown(ctx: VerifyContext, outcomes: list[CriterionOutcome], decision: dict, *, task: str, now: str) -> str:
    run_id = ctx.evidence_dir.name
    run_end = (ctx.of_type("run_end") or [{}])[-1]
    result = ctx.result or {}
    run = ctx.run
    lines = [
        f"# Agent run report: {run_id}",
        "",
        "| 項目 | 値 |",
        "| --- | --- |",
        f"| task | {task} |",
        f"| contract | `{ctx.contract.data['contract_id']}` v{ctx.contract.data['contract_version']} (`{(ctx.contract.data.get('contract_hash') or '')[:19]}...`) |",
        f"| 判定 | **{decision['status']}** |",
        f"| stop_reason | {ctx.stop_reason or 'なし'} |",
        f"| steps / tool_calls / blocks | {run_end.get('steps', '-')} / {run_end.get('tool_calls', '-')} / {run_end.get('blocks', '-')} |",
    ]
    if run is not None:
        lines.append(f"| pi | {run.duration_s}s, turns={run.turns}, settled={run.settled}, exit={run.exit_code}{', error=' + run.error if run.error else ''}{', aborted_turns=' + str(run.aborted_turns) if run.aborted_turns else ''} |")
    lines += [f"| 日時 | {now} |", "", "## Completion Criteria", "", "| ID | 必須 | 結果 | 備考 |", "| --- | --- | --- | --- |"]
    crit_by_id = {c["criterion_id"]: c for c in ctx.contract.criteria}
    for o in outcomes:
        req = "yes" if crit_by_id.get(o.criterion_id, {}).get("required") else "no"
        lines.append(f"| {o.criterion_id} | {req} | **{o.status}** | {o.notes} |")
    lines += ["", "## Tool calls (events.jsonl)", "", "| seq | type | tool | 備考 |", "| --- | --- | --- | --- |"]
    for e in ctx.events:
        t = e.get("type")
        if t in ("tool_call", "block", "tool_result", "stop", "finish"):
            note = e.get("reason") or (("error: " if e.get("is_error") else "") + str(e.get("result_preview", ""))[:80].replace("\n", " ").replace("|", "\\|") if t == "tool_result" else "")
            if t == "tool_call":
                note = json.dumps(e.get("args", {}), ensure_ascii=False)[:100].replace("|", "\\|")
            if t == "stop":
                note = f"stop_reason={e.get('stop_reason')}"
            if t == "finish":
                note = f"sources={e.get('sources')}"
            lines.append(f"| {e.get('seq')} | {t} | {e.get('tool', '')} | {note} |")
    if result:
        lines += ["", "## Agent result (finish, 未検証の自己申告)", "", f"sources: {', '.join(map(str, result.get('sources', [])))}", "",
                  str(result.get("summary", "")).strip(), ""]
    lines += ["", "## Reasons", ""] + [f"- {r}" for r in decision["reasons"]]
    return "\n".join(lines) + "\n"


def write_outputs(ctx: VerifyContext, outcomes: list[CriterionOutcome], *, task: str, schema_dir: Path | None = None,
                  now: str | None = None) -> tuple[dict, dict[str, Path]]:
    decision, verifications, md = build_outputs(ctx, outcomes, task=task, now=now)
    if schema_dir is not None:
        _, errors = validate_against_schema(decision, "completion-decision", schema_dir)
        for v in verifications:
            _, e2 = validate_against_schema(v, "verification-result", schema_dir)
            errors += [f"{v['criterion_id']}: {e}" for e in e2]
        if errors:
            decision["unresolved_errors"].append({"severity": "medium", "code": "SCHEMA_ERRORS",
                                                  "description": "; ".join(errors)[:3900]})
    paths = {
        "decision": ctx.evidence_dir / "decision.json",
        "verification": ctx.evidence_dir / "verification.json",
        "report": ctx.evidence_dir / "report.md",
    }
    paths["decision"].write_text(json.dumps(decision, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["verification"].write_text(json.dumps(verifications, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["report"].write_text(md, encoding="utf-8")
    return decision, paths
