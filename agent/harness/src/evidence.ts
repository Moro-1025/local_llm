/**
 * EvidenceLog: append-only JSONL under evidence/<run_id>/events.jsonl.
 * Every record carries run_id, contract_hash, a sequence number and an ISO timestamp.
 */

import { appendFileSync, mkdirSync, writeFileSync } from "node:fs";
import * as nodePath from "node:path";

export const RESULT_PREVIEW_CHARS = 500;

export interface EvidenceRecord {
  seq: number;
  ts: string;
  run_id: string;
  contract_hash: string;
  type: string;
  [key: string]: unknown;
}

export function preview(text: string, limit = RESULT_PREVIEW_CHARS): string {
  return text.length > limit ? text.slice(0, limit) : text;
}

/** Concatenate the text parts of a Pi tool result ({ content: [...] }) or of a bare content array. */
export function resultText(result: unknown): string {
  if (typeof result === "string") return result;
  if (!result || typeof result !== "object") return "";
  const content = Array.isArray(result) ? result : (result as { content?: unknown }).content;
  if (!Array.isArray(content)) return "";
  const parts: string[] = [];
  for (const c of content) {
    if (c && typeof c === "object" && (c as { type?: string }).type === "text") {
      parts.push(String((c as { text?: unknown }).text ?? ""));
    }
  }
  return parts.join("\n");
}

export class EvidenceLog {
  readonly dir: string;
  readonly eventsPath: string;
  private seq = 0;

  constructor(dir: string, readonly runId: string, readonly contractHash: string) {
    this.dir = nodePath.resolve(dir);
    this.eventsPath = nodePath.join(this.dir, "events.jsonl");
    mkdirSync(this.dir, { recursive: true });
  }

  append(type: string, data: Record<string, unknown> = {}): EvidenceRecord {
    this.seq += 1;
    const record: EvidenceRecord = {
      seq: this.seq,
      ts: new Date().toISOString(),
      run_id: this.runId,
      contract_hash: this.contractHash,
      type,
      ...data,
    };
    appendFileSync(this.eventsPath, JSON.stringify(record) + "\n", "utf8");
    return record;
  }

  /** Write a side file (e.g. result.json) in the evidence directory. */
  writeFile(name: string, value: unknown): string {
    const p = nodePath.join(this.dir, name);
    writeFileSync(p, JSON.stringify(value, null, 2) + "\n", "utf8");
    return p;
  }
}
