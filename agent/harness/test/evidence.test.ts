import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import * as nodePath from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { EvidenceLog, preview, RESULT_PREVIEW_CHARS, resultText } from "../src/evidence.ts";

let dir: string;
afterEach(() => {
  if (dir && existsSync(dir)) rmSync(dir, { recursive: true, force: true });
});

describe("EvidenceLog", () => {
  it("appends numbered JSONL records with run_id and contract_hash", () => {
    dir = mkdtempSync(nodePath.join(tmpdir(), "harness-ev-"));
    const log = new EvidenceLog(nodePath.join(dir, "run-1"), "run-1", "sha256:abc");
    log.append("run_start", { a: 1 });
    log.append("block", { tool: "bash", code: "tool_not_allowed" });
    const lines = readFileSync(log.eventsPath, "utf8").trim().split("\n").map((l) => JSON.parse(l));
    expect(lines).toHaveLength(2);
    expect(lines[0]).toMatchObject({ seq: 1, type: "run_start", run_id: "run-1", contract_hash: "sha256:abc", a: 1 });
    expect(lines[1]).toMatchObject({ seq: 2, type: "block", tool: "bash", code: "tool_not_allowed" });
    expect(typeof lines[1].ts).toBe("string");
  });
  it("writeFile stores pretty JSON next to events.jsonl", () => {
    dir = mkdtempSync(nodePath.join(tmpdir(), "harness-ev-"));
    const log = new EvidenceLog(dir, "r", "sha256:x");
    const p = log.writeFile("result.json", { summary: "s", sources: ["docs/a.md"] });
    expect(JSON.parse(readFileSync(p, "utf8"))).toEqual({ summary: "s", sources: ["docs/a.md"] });
  });
});

describe("preview / resultText", () => {
  it("preview keeps the first 500 chars", () => {
    const long = "x".repeat(1200);
    expect(preview(long)).toHaveLength(RESULT_PREVIEW_CHARS);
    expect(preview("short")).toBe("short");
  });
  it("resultText joins text parts of a tool result", () => {
    expect(resultText({ content: [{ type: "text", text: "a" }, { type: "image", data: "" }, { type: "text", text: "b" }] })).toBe("a\nb");
    expect(resultText([{ type: "text", text: "only" }])).toBe("only");
    expect(resultText("raw")).toBe("raw");
    expect(resultText(undefined)).toBe("");
  });
});
