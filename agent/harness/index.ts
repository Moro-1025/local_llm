/**
 * Harness extension for Pi (pi-coding-agent).
 *
 * Loads the Task Contract named by HARNESS_CONTRACT, enforces it on every tool call, records
 * evidence under HARNESS_EVIDENCE_DIR/events.jsonl and registers the read-only tools
 * rag_search / read_file / list_files / finish.
 *
 * Environment (set by src/local_llm/agent/runner.py):
 *   HARNESS_CONTRACT      path to the contract JSON (required)
 *   HARNESS_RUN_ID        run id (default: timestamp)
 *   HARNESS_EVIDENCE_DIR  directory for events.jsonl / result.json (default: evidence/<run_id>)
 *   HARNESS_WORKSPACE     workspace root for scope checks (default: process.cwd())
 *   HARNESS_RAG_URL       base URL of llm-web for rag_search (default: http://127.0.0.1:8000)
 *   HARNESS_BUDGET        JSON object overriding execution_budget fields (e.g. {"max_steps":2})
 *   HARNESS_MAX_FILE_CHARS / HARNESS_RAG_TEXT_CHARS / HARNESS_MAX_OUTPUT_CHARS
 *                         tool output limits (defaults 3000 / 350 / 6000 chars, sized for an 8k context)
 *   HARNESS_THINKING      "off" adds chat_template_kwargs.enable_thinking=false to every llama-server request
 *                         (Pi's --thinking off is not understood by llama-server's OpenAI-compatible endpoint)
 */

import { readFileSync } from "node:fs";
import * as nodePath from "node:path";
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { EvidenceLog, preview, resultText } from "./src/evidence.ts";
import { type Contract, HarnessPolicy } from "./src/policy.ts";
import { makeTools } from "./src/tools.ts";

function loadContract(path: string): Contract {
  const raw = readFileSync(path, "utf8");
  return JSON.parse(raw) as Contract;
}

export default function (pi: ExtensionAPI) {
  const contractPath = process.env.HARNESS_CONTRACT;
  if (!contractPath) {
    throw new Error("HARNESS_CONTRACT is not set; the harness extension needs a Task Contract");
  }
  const workspace = nodePath.resolve(process.env.HARNESS_WORKSPACE ?? process.cwd());
  const runId = process.env.HARNESS_RUN_ID ?? new Date().toISOString().replace(/[:.]/g, "-");
  const evidenceDir = process.env.HARNESS_EVIDENCE_DIR ?? nodePath.join(workspace, "evidence", runId);
  const ragUrl = process.env.HARNESS_RAG_URL ?? "http://127.0.0.1:8000";
  const budget = process.env.HARNESS_BUDGET ? JSON.parse(process.env.HARNESS_BUDGET) : undefined;

  const contract = loadContract(nodePath.resolve(workspace, contractPath));
  const policy = new HarnessPolicy(contract, { workspace, budget });
  const log = new EvidenceLog(evidenceDir, runId, policy.contractHash);

  log.append("run_start", {
    contract_id: contract.contract_id,
    task_id: contract.task_id,
    contract_version: contract.contract_version,
    contract_path: contractPath,
    hash_matches_contract: contract.contract_hash === policy.contractHash,
    workspace,
    allowed_tools: policy.allowedTools,
    budget: policy.budget,
  });

  const toolOptions = {
    ragUrl,
    maxFileChars: intEnv("HARNESS_MAX_FILE_CHARS"),
    ragTextChars: intEnv("HARNESS_RAG_TEXT_CHARS"),
    maxTotalOutputChars: intEnv("HARNESS_MAX_OUTPUT_CHARS"),
  };
  for (const tool of makeTools(policy, log, toolOptions)) {
    // Type.Object from typebox is structurally compatible with Pi's TSchema parameters.
    pi.registerTool(tool as never);
  }

  if (process.env.HARNESS_THINKING === "off") {
    pi.on("before_provider_request", async (event) => {
      const payload = event.payload as Record<string, unknown> | null;
      if (payload && typeof payload === "object") {
        const kwargs = (payload.chat_template_kwargs as Record<string, unknown> | undefined) ?? {};
        payload.chat_template_kwargs = { ...kwargs, enable_thinking: false };
      }
      return payload;
    });
  }

  pi.on("turn_start", async (event, ctx: ExtensionContext) => {
    const decision = policy.onTurnStart();
    log.append("turn_start", { turn_index: event.turnIndex, step: policy.steps });
    if (decision.block) {
      log.append("stop", { stop_reason: policy.stopReason, code: decision.code, reason: decision.reason });
      ctx.abort();
    }
  });

  pi.on("tool_call", async (event) => {
    const input = (event.input ?? {}) as Record<string, unknown>;
    const decision = policy.decideToolCall(event.toolName, input);
    const base = {
      tool_call_id: event.toolCallId,
      tool: event.toolName,
      args_hash: hashOf(event.toolName, input),
      args: input,
    };
    if (decision.block) {
      log.append("block", { ...base, code: decision.code, reason: decision.reason, stop_reason: policy.stopReason });
      return { block: true, reason: `[harness] ${decision.code}: ${decision.reason}`, terminate: policy.mustStop() };
    }
    log.append("tool_call", base);
    return undefined;
  });

  pi.on("tool_result", async (event) => {
    const body = resultText({ content: event.content });
    const decision = policy.recordResult(event.toolName, event.input, Boolean(event.isError), body);
    log.append("tool_result", {
      tool_call_id: event.toolCallId,
      tool: event.toolName,
      is_error: Boolean(event.isError),
      result_preview: preview(body),
      result_chars: body.length,
    });
    if (decision.block) {
      log.append("stop", { stop_reason: policy.stopReason, code: decision.code, reason: decision.reason });
    }
    return undefined;
  });

  pi.on("agent_before_settle", async () => {
    if (policy.mustStop()) {
      log.append("settle", { continue: false, stop_reason: policy.stopReason });
      return { continue: false };
    }
    return undefined;
  });

  pi.on("agent_end", async () => {
    log.append("run_end", policy.summary());
  });
}

import { argsHash } from "./src/policy.ts";
function hashOf(toolName: string, input: unknown): string {
  return argsHash(toolName, input);
}

function intEnv(name: string): number | undefined {
  const v = process.env[name];
  if (!v) return undefined;
  const n = Number.parseInt(v, 10);
  return Number.isFinite(n) && n > 0 ? n : undefined;
}
