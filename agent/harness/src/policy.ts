/**
 * HarnessPolicy: the code that decides. The model proposes tool calls; this class allows or blocks
 * them from the Task Contract alone (LLM_PROPOSES_CODE_DECIDES).
 *
 *  - capability_profile.allowed_tools / denied_tools      -> tool_not_allowed
 *  - scope.read / scope.write / scope.forbidden (globs)    -> scope_boundary_exceeded
 *  - execution_budget: max_steps, max_tool_calls,
 *    same_action_limit, same_error_limit                   -> budget_exceeded / same_error_repeated
 *
 * Pure TypeScript with no Pi dependency so it can be unit-tested with vitest.
 */

import { createHash } from "node:crypto";
import * as nodePath from "node:path";
import { matchesAny, normalizeRelative } from "./glob.ts";

export interface ContractScope {
  read: string[];
  write: string[];
  forbidden: string[];
}

export interface ExecutionBudget {
  max_steps?: number | null;
  max_tool_calls?: number | null;
  same_action_limit?: number | null;
  same_error_limit?: number | null;
  on_exceed?: "stop" | "escalate";
}

export interface Contract {
  contract_id: string;
  task_id: string;
  contract_version: number;
  contract_hash?: string | null;
  scope: ContractScope;
  capability_profile: { allowed_tools: string[]; denied_tools: string[] };
  execution_budget: ExecutionBudget;
  completion_criteria?: { criterion_id: string; assertion: string; required: boolean }[];
  [key: string]: unknown;
}

export type BlockCode =
  | "tool_not_allowed"
  | "scope_boundary_exceeded"
  | "budget_exceeded"
  | "same_error_repeated"
  | "already_finished"
  | "run_stopped";

export type Decision = { block: false } | { block: true; code: BlockCode; reason: string };

export type StopReason = "budget_exceeded" | "same_error_repeated" | "scope_violation" | "finished";

export interface PolicyOptions {
  /** Absolute workspace root; paths outside it are always out of scope. */
  workspace: string;
  /** Budget overrides (e.g. max_steps=2 for AGT-003). */
  budget?: Partial<ExecutionBudget>;
}

/** Canonical JSON: keys sorted, minimal separators (same as Python json.dumps(sort_keys=True, separators=(",", ":"))). */
export function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return "[" + value.map(canonicalJson).join(",") + "]";
  const obj = value as Record<string, unknown>;
  const keys = Object.keys(obj).sort();
  return "{" + keys.map((k) => JSON.stringify(k) + ":" + canonicalJson(obj[k])).join(",") + "}";
}

/** sha256 of the contract with contract_hash set to null (same algorithm as scripts/agent/check_contracts.py). */
export function contractHash(contract: Record<string, unknown>): string {
  const body = { ...contract, contract_hash: null };
  return "sha256:" + createHash("sha256").update(canonicalJson(body), "utf8").digest("hex");
}

/** Normalized hash of tool arguments, used to detect repeated identical actions. */
export function argsHash(toolName: string, input: unknown): string {
  return "sha256:" + createHash("sha256").update(canonicalJson({ tool: toolName, input }), "utf8").digest("hex");
}

/**
 * Which argument of a tool names a path, and whether the call reads or writes it.
 * `list_files` is deliberately absent: a broad glob such as `**\/*.md` is not a violation because the
 * tool itself filters its output to scope.read (nothing outside scope is ever revealed).
 */
const PATH_ARGS: Record<string, { key: string; mode: "read" | "write" }> = {
  read_file: { key: "path", mode: "read" },
  read: { key: "path", mode: "read" },
  edit: { key: "path", mode: "write" },
  write: { key: "path", mode: "write" },
  grep: { key: "path", mode: "read" },
  find: { key: "path", mode: "read" },
  ls: { key: "path", mode: "read" },
};

export class HarnessPolicy {
  readonly contract: Contract;
  readonly contractHash: string;
  readonly workspace: string;
  readonly budget: ExecutionBudget;

  steps = 0;
  toolCalls = 0;
  blocks = 0;
  finished = false;
  stopReason: StopReason | null = null;
  private readonly actionCounts = new Map<string, number>();
  private lastErrorHash: string | null = null;
  private sameErrorCount = 0;

  constructor(contract: Contract, options: PolicyOptions) {
    this.contract = contract;
    this.contractHash = contractHash(contract);
    this.workspace = nodePath.resolve(options.workspace);
    this.budget = { ...contract.execution_budget, ...stripUndefined(options.budget ?? {}) };
  }

  get allowedTools(): string[] {
    return this.contract.capability_profile.allowed_tools;
  }

  /** Resolve a tool argument to a workspace-relative path, or null when it escapes the workspace. */
  relativePath(input: string): string | null {
    const abs = nodePath.resolve(this.workspace, input);
    const rel = nodePath.relative(this.workspace, abs);
    if (rel === "") return ".";
    if (rel.startsWith("..") || nodePath.isAbsolute(rel)) return null;
    return normalizeRelative(rel);
  }

  /** Scope check for one path. `mode` picks scope.read or scope.write; forbidden always wins. */
  checkPath(input: string, mode: "read" | "write"): Decision {
    const rel = this.relativePath(input);
    if (rel === null) {
      return { block: true, code: "scope_boundary_exceeded", reason: `path escapes the workspace: ${input}` };
    }
    if (matchesAny(rel, this.contract.scope.forbidden)) {
      return { block: true, code: "scope_boundary_exceeded", reason: `path is forbidden by scope.forbidden: ${rel}` };
    }
    const allowed = mode === "read" ? this.contract.scope.read : this.contract.scope.write;
    if (!matchesAny(rel, allowed)) {
      return { block: true, code: "scope_boundary_exceeded", reason: `path is outside scope.${mode}: ${rel}` };
    }
    return { block: false };
  }

  /** Called at the start of every agent turn. Returns a block decision once max_steps is exhausted. */
  onTurnStart(): Decision {
    this.steps += 1;
    const max = this.budget.max_steps;
    if (max != null && this.steps > max) {
      this.stopReason = this.stopReason ?? "budget_exceeded";
      return { block: true, code: "budget_exceeded", reason: `max_steps=${max} exceeded (step ${this.steps})` };
    }
    return { block: false };
  }

  /** Decide whether a tool call may run. Counts the call either way. */
  decideToolCall(toolName: string, input: Record<string, unknown>): Decision {
    this.toolCalls += 1;
    const decision = this.evaluate(toolName, input);
    if (decision.block) {
      this.blocks += 1;
      if (decision.code === "scope_boundary_exceeded") {
        this.stopReason = this.stopReason ?? "scope_violation";
      }
    }
    return decision;
  }

  private evaluate(toolName: string, input: Record<string, unknown>): Decision {
    if (this.mustStop()) {
      return { block: true, code: "run_stopped", reason: `run already stopped: ${this.stopReason}` };
    }
    if (this.finished) {
      return { block: true, code: "already_finished", reason: "finish() was already called; no further tool calls" };
    }
    if (!this.allowedTools.includes(toolName) || this.contract.capability_profile.denied_tools.includes(toolName)) {
      return { block: true, code: "tool_not_allowed", reason: `tool '${toolName}' is not in capability_profile.allowed_tools` };
    }

    const maxCalls = this.budget.max_tool_calls;
    if (maxCalls != null && this.toolCalls > maxCalls) {
      this.stopReason = this.stopReason ?? "budget_exceeded";
      return { block: true, code: "budget_exceeded", reason: `max_tool_calls=${maxCalls} exceeded` };
    }
    const maxSteps = this.budget.max_steps;
    if (maxSteps != null && this.steps > maxSteps) {
      this.stopReason = this.stopReason ?? "budget_exceeded";
      return { block: true, code: "budget_exceeded", reason: `max_steps=${maxSteps} exceeded (step ${this.steps})` };
    }

    const hash = argsHash(toolName, input);
    const count = (this.actionCounts.get(hash) ?? 0) + 1;
    this.actionCounts.set(hash, count);
    const sameAction = this.budget.same_action_limit;
    if (sameAction != null && count > sameAction) {
      this.stopReason = this.stopReason ?? "budget_exceeded";
      return { block: true, code: "budget_exceeded", reason: `same_action_limit=${sameAction} exceeded for ${toolName}` };
    }

    const pathRule = PATH_ARGS[toolName];
    if (pathRule) {
      const raw = input[pathRule.key];
      if (typeof raw === "string" && raw.length > 0) {
        const check = this.checkPath(raw, pathRule.mode);
        if (check.block) return check;
      }
    }
    return { block: false };
  }

  /** Called after a tool ran. Tracks repeated identical failures (same_error_limit). */
  recordResult(toolName: string, input: unknown, isError: boolean, errorText?: string): Decision {
    if (!isError) {
      this.lastErrorHash = null;
      this.sameErrorCount = 0;
      return { block: false };
    }
    const hash = argsHash(toolName, { input, error: (errorText ?? "").slice(0, 200) });
    if (hash === this.lastErrorHash) {
      this.sameErrorCount += 1;
    } else {
      this.lastErrorHash = hash;
      this.sameErrorCount = 1;
    }
    const limit = this.budget.same_error_limit;
    if (limit != null && this.sameErrorCount >= limit) {
      this.stopReason = this.stopReason ?? "same_error_repeated";
      return {
        block: true,
        code: "same_error_repeated",
        reason: `same error repeated ${this.sameErrorCount} times (limit ${limit})`,
      };
    }
    return { block: false };
  }

  markFinished(): void {
    this.finished = true;
    this.stopReason = this.stopReason ?? "finished";
  }

  /** True when the agent must not continue (anything but a clean finish). */
  mustStop(): boolean {
    return this.stopReason !== null && this.stopReason !== "finished";
  }

  summary() {
    return {
      steps: this.steps,
      tool_calls: this.toolCalls,
      blocks: this.blocks,
      finished: this.finished,
      stop_reason: this.stopReason,
      budget: this.budget,
    };
  }
}

function stripUndefined<T extends object>(obj: T): Partial<T> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(obj)) if (v !== undefined) out[k] = v;
  return out as Partial<T>;
}
