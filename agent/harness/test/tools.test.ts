import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import * as nodePath from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { EvidenceLog } from "../src/evidence.ts";
import { HarnessPolicy } from "../src/policy.ts";
import { listWorkspaceFiles, makeTools } from "../src/tools.ts";

let dir: string;
afterEach(() => {
  if (dir && existsSync(dir)) rmSync(dir, { recursive: true, force: true });
});

function workspace(): string {
  dir = mkdtempSync(nodePath.join(tmpdir(), "harness-ws-"));
  mkdirSync(nodePath.join(dir, "docs", "sub"), { recursive: true });
  mkdirSync(nodePath.join(dir, "src"), { recursive: true });
  writeFileSync(nodePath.join(dir, "docs", "a.md"), "# A\nhello", "utf8");
  writeFileSync(nodePath.join(dir, "docs", "sub", "b.md"), "# B", "utf8");
  writeFileSync(nodePath.join(dir, "src", "x.py"), "x = 1", "utf8");
  writeFileSync(nodePath.join(dir, "README.md"), "# readme", "utf8");
  writeFileSync(nodePath.join(dir, ".env"), "SECRET=1", "utf8");
  return dir;
}

function setup(toolOptions: Record<string, unknown> = {}) {
  const ws = workspace();
  const policy = new HarnessPolicy(
    {
      contract_id: "c", task_id: "t", contract_version: 1,
      scope: { read: ["docs/**", "README.md"], write: [], forbidden: [".env"] },
      capability_profile: { allowed_tools: ["rag_search", "read_file", "list_files", "finish"], denied_tools: ["bash"] },
      execution_budget: { on_exceed: "stop" },
    },
    { workspace: ws },
  );
  const log = new EvidenceLog(nodePath.join(ws, "evidence", "r1"), "r1", policy.contractHash);
  const tools = Object.fromEntries(makeTools(policy, log, { ragUrl: "http://127.0.0.1:9", ...toolOptions }).map((t) => [t.name, t]));
  return { ws, policy, log, tools };
}

describe("listWorkspaceFiles", () => {
  it("walks the workspace and matches the glob against relative paths", () => {
    const ws = workspace();
    expect(listWorkspaceFiles(ws, "**/*.md", 100)).toEqual(["README.md", "docs/a.md", "docs/sub/b.md"]);
    expect(listWorkspaceFiles(ws, "docs/*.md", 100)).toEqual(["docs/a.md"]);
    expect(listWorkspaceFiles(ws, "**/*.md", 1)).toHaveLength(1);
  });
});

describe("tools", () => {
  it("list_files hides files outside scope.read even for a broad glob", async () => {
    const { tools } = setup();
    const res = await tools.list_files.execute("id", { glob: "**/*" } as never, undefined, undefined, {} as never);
    const listed = (res.content[0] as { text: string }).text.split("\n");
    expect(listed).toEqual(["README.md", "docs/a.md", "docs/sub/b.md"]);
    expect(listed).not.toContain("src/x.py");
    expect(listed).not.toContain(".env");
  });
  it("read_file reads inside scope and refuses outside / forbidden paths", async () => {
    const { tools } = setup();
    const ok = await tools.read_file.execute("id", { path: "docs/a.md" } as never, undefined, undefined, {} as never);
    expect((ok.content[0] as { text: string }).text).toBe("# A\nhello");
    const no = await tools.read_file.execute("id", { path: "src/x.py" } as never, undefined, undefined, {} as never);
    expect(no.isError).toBe(true);
    const env = await tools.read_file.execute("id", { path: ".env" } as never, undefined, undefined, {} as never);
    expect(env.isError).toBe(true);
    expect((env.content[0] as { text: string }).text).toContain("forbidden");
  });
  it("read-only tools share an output budget and tell the model to finish once it is spent", async () => {
    const { tools } = setup({ maxTotalOutputChars: 220 });
    const first = await tools.read_file.execute("id", { path: "docs/a.md" } as never, undefined, undefined, {} as never);
    expect(first.isError).toBe(false); // 9 chars spent
    const list = await tools.list_files.execute("id", { glob: "**/*" } as never, undefined, undefined, {} as never);
    expect(list.isError).toBe(false); // ~35 more
    // 220 - 44 = 176 left (< 200) -> exhausted
    const again = await tools.read_file.execute("id", { path: "docs/sub/b.md" } as never, undefined, undefined, {} as never);
    expect(again.isError).toBe(true);
    expect((again.content[0] as { text: string }).text).toContain("Call finish now");
  });
  it("finish writes result.json, logs a finish record and marks the policy finished", async () => {
    const { tools, policy, log, ws } = setup();
    const res = await tools.finish.execute("id", { summary: "three points", sources: ["docs/a.md"] } as never, undefined, undefined, {} as never);
    expect(res.isError).toBe(false);
    expect(policy.finished).toBe(true);
    expect(existsSync(nodePath.join(ws, "evidence", "r1", "result.json"))).toBe(true);
    expect(existsSync(log.eventsPath)).toBe(true);
  });
});
