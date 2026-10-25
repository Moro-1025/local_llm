import { describe, expect, it } from "vitest";
import { argsHash, canonicalJson, type Contract, contractHash, HarnessPolicy } from "../src/policy.ts";

const WORKSPACE = process.platform === "win32" ? "C:\\work\\repo" : "/work/repo";

function contract(overrides: Partial<Contract> = {}): Contract {
  return {
    contract_id: "contract:test",
    task_id: "TEST-1",
    contract_version: 1,
    contract_hash: null,
    scope: {
      read: ["docs/**", "README.md", "data/corpus/**"],
      write: [],
      forbidden: [".env", "**/*.gguf", ".git/**"],
    },
    capability_profile: {
      allowed_tools: ["rag_search", "read_file", "list_files", "finish"],
      denied_tools: ["bash", "write", "edit"],
    },
    execution_budget: { max_steps: 10, max_tool_calls: 20, same_action_limit: 3, same_error_limit: 2, on_exceed: "stop" },
    ...overrides,
  };
}

describe("canonicalJson / contractHash", () => {
  it("sorts keys and uses minimal separators (matches Python json.dumps(sort_keys=True, separators=(',', ':')))", () => {
    expect(canonicalJson({ b: 1, a: [true, null, "x"], c: { z: 1, y: 2 } })).toBe('{"a":[true,null,"x"],"b":1,"c":{"y":2,"z":1}}');
  });
  it("ignores the stored contract_hash", () => {
    const a = contractHash({ x: 1, contract_hash: "sha256:old" });
    const b = contractHash({ x: 1, contract_hash: null });
    expect(a).toBe(b);
    expect(a).toMatch(/^sha256:[0-9a-f]{64}$/);
  });
  it("argsHash is order-independent for object keys", () => {
    expect(argsHash("t", { a: 1, b: 2 })).toBe(argsHash("t", { b: 2, a: 1 }));
    expect(argsHash("t", { a: 1 })).not.toBe(argsHash("u", { a: 1 }));
  });
});

describe("HarnessPolicy: allowed tools (AGT-001)", () => {
  it("blocks bash with tool_not_allowed and counts the block", () => {
    const p = new HarnessPolicy(contract(), { workspace: WORKSPACE });
    const d = p.decideToolCall("bash", { command: "ls" });
    expect(d).toMatchObject({ block: true, code: "tool_not_allowed" });
    expect(p.blocks).toBe(1);
    expect(p.toolCalls).toBe(1);
    expect(p.stopReason).toBeNull(); // a denied tool does not stop the run by itself
  });
  it("allows tools in allowed_tools", () => {
    const p = new HarnessPolicy(contract(), { workspace: WORKSPACE });
    expect(p.decideToolCall("rag_search", { query: "x" })).toEqual({ block: false });
    expect(p.decideToolCall("list_files", { glob: "docs/**/*.md" })).toEqual({ block: false });
  });
  it("a tool in both lists is denied", () => {
    const c = contract();
    c.capability_profile.denied_tools.push("read_file");
    const p = new HarnessPolicy(c, { workspace: WORKSPACE });
    expect(p.decideToolCall("read_file", { path: "README.md" })).toMatchObject({ block: true, code: "tool_not_allowed" });
  });
});

describe("HarnessPolicy: scope (AGT-002)", () => {
  it("blocks ../ escapes and absolute paths outside the workspace, and the run stops (scope_violation)", () => {
    const p = new HarnessPolicy(contract(), { workspace: WORKSPACE });
    expect(p.decideToolCall("read_file", { path: "../secrets.txt" })).toMatchObject({ block: true, code: "scope_boundary_exceeded" });
    expect(p.stopReason).toBe("scope_violation");
    expect(p.mustStop()).toBe(true);
    // after a violation every further call is refused
    expect(p.decideToolCall("read_file", { path: "docs/a.md" })).toMatchObject({ block: true, code: "run_stopped" });

    const outside = process.platform === "win32" ? "C:\\Windows\\win.ini" : "/etc/passwd";
    const q = new HarnessPolicy(contract(), { workspace: WORKSPACE });
    expect(q.decideToolCall("read_file", { path: outside })).toMatchObject({ block: true, code: "scope_boundary_exceeded" });
  });
  it("blocks paths inside the workspace but outside scope.read, and forbidden paths", () => {
    const p = new HarnessPolicy(contract(), { workspace: WORKSPACE });
    expect(p.decideToolCall("read_file", { path: "src/local_llm/cli.py" })).toMatchObject({ block: true, code: "scope_boundary_exceeded" });
    const wide = { read: ["**"], write: [], forbidden: [".env", "**/*.gguf"] };
    const p2 = new HarnessPolicy(contract({ scope: wide }), { workspace: WORKSPACE });
    expect(p2.decideToolCall("read_file", { path: ".env" })).toMatchObject({ block: true, code: "scope_boundary_exceeded" });
    const p3 = new HarnessPolicy(contract({ scope: wide }), { workspace: WORKSPACE });
    expect(p3.decideToolCall("read_file", { path: "models/x.gguf" })).toMatchObject({ block: true, code: "scope_boundary_exceeded" });
  });
  it("allows paths inside scope.read, with either slash style", () => {
    const p = new HarnessPolicy(contract(), { workspace: WORKSPACE });
    expect(p.decideToolCall("read_file", { path: "docs/spec.md" })).toEqual({ block: false });
    expect(p.decideToolCall("read_file", { path: "docs\\journal\\phase-3.md" })).toEqual({ block: false });
    expect(p.decideToolCall("read_file", { path: "./README.md" })).toEqual({ block: false });
  });
  it("list_files is not blocked for a broad glob (the tool filters its output to scope.read)", () => {
    const p = new HarnessPolicy(contract(), { workspace: WORKSPACE });
    expect(p.decideToolCall("list_files", { glob: "docs/**/*.md" })).toEqual({ block: false });
    expect(p.decideToolCall("list_files", { glob: "**/*.md" })).toEqual({ block: false });
    expect(p.stopReason).toBeNull();
  });
  it("built-in write tools need scope.write", () => {
    const c = contract();
    c.capability_profile.allowed_tools.push("write");
    c.capability_profile.denied_tools = c.capability_profile.denied_tools.filter((t) => t !== "write");
    const p = new HarnessPolicy(c, { workspace: WORKSPACE });
    expect(p.decideToolCall("write", { path: "docs/new.md", content: "" })).toMatchObject({ block: true, code: "scope_boundary_exceeded" });
  });
});

describe("HarnessPolicy: budgets (AGT-003)", () => {
  it("max_steps: the (max+1)th turn is blocked and stop_reason=budget_exceeded", () => {
    const p = new HarnessPolicy(contract(), { workspace: WORKSPACE, budget: { max_steps: 2 } });
    expect(p.onTurnStart()).toEqual({ block: false });
    expect(p.onTurnStart()).toEqual({ block: false });
    expect(p.onTurnStart()).toMatchObject({ block: true, code: "budget_exceeded" });
    expect(p.stopReason).toBe("budget_exceeded");
    expect(p.mustStop()).toBe(true);
    // every later tool call is blocked too
    expect(p.decideToolCall("rag_search", { query: "x" })).toMatchObject({ block: true, code: "run_stopped" });
  });
  it("max_tool_calls", () => {
    const p = new HarnessPolicy(contract(), { workspace: WORKSPACE, budget: { max_tool_calls: 2 } });
    p.decideToolCall("rag_search", { query: "a" });
    p.decideToolCall("rag_search", { query: "b" });
    expect(p.decideToolCall("rag_search", { query: "c" })).toMatchObject({ block: true, code: "budget_exceeded" });
    expect(p.stopReason).toBe("budget_exceeded");
  });
  it("same_action_limit blocks the identical call after N repeats", () => {
    const p = new HarnessPolicy(contract(), { workspace: WORKSPACE, budget: { same_action_limit: 2 } });
    expect(p.decideToolCall("read_file", { path: "docs/a.md" })).toEqual({ block: false });
    expect(p.decideToolCall("read_file", { path: "docs/a.md" })).toEqual({ block: false });
    expect(p.decideToolCall("read_file", { path: "docs/a.md" })).toMatchObject({ block: true, code: "budget_exceeded" });
  });
  it("same_error_limit stops after the same error twice", () => {
    const p = new HarnessPolicy(contract(), { workspace: WORKSPACE });
    expect(p.recordResult("read_file", { path: "docs/missing.md" }, true, "ENOENT")).toEqual({ block: false });
    expect(p.recordResult("read_file", { path: "docs/missing.md" }, true, "ENOENT")).toMatchObject({ block: true, code: "same_error_repeated" });
    expect(p.stopReason).toBe("same_error_repeated");
  });
  it("a success resets the same-error counter", () => {
    const p = new HarnessPolicy(contract(), { workspace: WORKSPACE });
    p.recordResult("read_file", { path: "x" }, true, "E");
    p.recordResult("read_file", { path: "y" }, false);
    expect(p.recordResult("read_file", { path: "x" }, true, "E")).toEqual({ block: false });
  });
  it("budget overrides replace only the given fields", () => {
    const p = new HarnessPolicy(contract(), { workspace: WORKSPACE, budget: { max_steps: 2, max_tool_calls: undefined } });
    expect(p.budget.max_steps).toBe(2);
    expect(p.budget.max_tool_calls).toBe(20);
  });
});

describe("HarnessPolicy: finish", () => {
  it("after finish, further tool calls are blocked but the run is not a failure", () => {
    const p = new HarnessPolicy(contract(), { workspace: WORKSPACE });
    p.markFinished();
    expect(p.stopReason).toBe("finished");
    expect(p.mustStop()).toBe(false);
    expect(p.decideToolCall("rag_search", { query: "x" })).toMatchObject({ block: true, code: "already_finished" });
    expect(p.summary()).toMatchObject({ finished: true, blocks: 1, tool_calls: 1 });
  });
});
