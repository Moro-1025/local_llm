/**
 * Custom read-only tools registered by the harness extension:
 *   rag_search(query, top_k)  -> GET {ragUrl}/api/rag/search  (Phase 3 retriever over HTTP)
 *   read_file(path)           -> file inside scope.read
 *   list_files(glob)          -> files inside scope.read
 *   finish(summary, sources)  -> submit the result; the runner verifies it (NO_SELF_REPORT)
 *
 * The tool_call hook in index.ts is the primary gate; these tools re-check paths as defense in depth.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import * as nodePath from "node:path";
import { Type } from "typebox";
import type { EvidenceLog } from "./evidence.ts";
import { globToRegExp, normalizeRelative } from "./glob.ts";
import type { HarnessPolicy } from "./policy.ts";

export interface ToolOptions {
  ragUrl: string;
  /** read_file truncates beyond this many characters (default 3000: the local model has an 8k context). */
  maxFileChars?: number;
  maxListEntries?: number;
  /** rag_search truncates each chunk's text to this many characters (default 350). */
  ragTextChars?: number;
  /** rag_search default / maximum top_k (default 3 / 3). */
  ragDefaultTopK?: number;
  ragMaxTopK?: number;
  /**
   * Total characters all tools may return in one run (default 6000). Once spent, read-only tools return
   * a short error telling the model to call finish. This keeps an 8k-context model from overflowing.
   */
  maxTotalOutputChars?: number;
}

/** Shape of the result the agent submits with finish(). */
export interface FinishResult {
  summary: string;
  sources: string[];
}

interface TextResult {
  content: { type: "text"; text: string }[];
  details: Record<string, unknown>;
  isError?: boolean;
}

function text(t: string, details: Record<string, unknown> = {}, isError = false): TextResult {
  return { content: [{ type: "text", text: t }], details, isError };
}

export function listWorkspaceFiles(workspace: string, glob: string, limit: number): string[] {
  const re = globToRegExp(glob);
  const out: string[] = [];
  const walk = (dir: string) => {
    if (out.length >= limit) return;
    let entries;
    try {
      entries = readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const e of entries) {
      if (out.length >= limit) return;
      if (e.name === "node_modules" || e.name === ".git" || e.name === ".venv") continue;
      const abs = nodePath.join(dir, e.name);
      const rel = normalizeRelative(nodePath.relative(workspace, abs));
      if (e.isDirectory()) walk(abs);
      else if (re.test(rel)) out.push(rel);
    }
  };
  walk(workspace);
  return out.sort();
}

export function makeTools(policy: HarnessPolicy, log: EvidenceLog, options: ToolOptions) {
  const maxFileChars = options.maxFileChars ?? 3000;
  const maxListEntries = options.maxListEntries ?? 200;
  const ragTextChars = options.ragTextChars ?? 350;
  const ragDefaultTopK = options.ragDefaultTopK ?? 3;
  const ragMaxTopK = options.ragMaxTopK ?? 3;
  const maxTotalOutputChars = options.maxTotalOutputChars ?? 6000;
  let spentChars = 0;

  /** Charge `body` against the shared output budget, truncating it to what is left. */
  function charge(body: string, toolName: string): TextResult | string {
    const remaining = maxTotalOutputChars - spentChars;
    if (remaining <= 200) {
      log.append("output_budget_exhausted", { tool: toolName, spent_chars: spentChars, limit: maxTotalOutputChars });
      return text(
        `tool output budget exhausted (${spentChars}/${maxTotalOutputChars} chars). Call finish now with what you already know.`,
        { code: "output_budget_exhausted" },
        true,
      );
    }
    const out = body.length > remaining ? body.slice(0, remaining) + `\n... (truncated: output budget, ${remaining} chars left)` : body;
    spentChars += out.length;
    return out;
  }

  const ragSearch = {
    name: "rag_search",
    label: "RAG search",
    description:
      "Search the indexed project documents (Phase 3 RAG) and return the most similar chunks with their file path, heading, score and a short excerpt. Use this first to find which documents are relevant.",
    parameters: Type.Object({
      query: Type.String({ description: "Natural-language question or keywords" }),
      top_k: Type.Optional(Type.Integer({ minimum: 1, maximum: 3, description: "Number of chunks (default 3, max 3)" })),
    }),
    async execute(_id: string, params: { query: string; top_k?: number }, signal?: AbortSignal) {
      const url = new URL("/api/rag/search", options.ragUrl);
      url.searchParams.set("q", params.query);
      url.searchParams.set("top_k", String(Math.min(params.top_k ?? ragDefaultTopK, ragMaxTopK)));
      const res = await fetch(url, { signal });
      if (!res.ok) {
        const body = await res.text();
        return text(`rag_search failed: HTTP ${res.status} ${body.slice(0, 300)}`, { status: res.status }, true);
      }
      // GET /api/rag/search returns {"query": ..., "results": [{n, path, title, heading, text, score}]}
      const data = (await res.json()) as { results?: unknown[]; items?: unknown[] } | unknown[];
      const items = (Array.isArray(data) ? data : data.results ?? data.items ?? []) as {
        n: number; path: string; heading: string; score: number; text: string;
      }[];
      const lines = items.map((it) => {
        const body = it.text.length > ragTextChars ? it.text.slice(0, ragTextChars) + " ..." : it.text;
        return `[${it.n}] ${it.path} › ${it.heading} (score ${it.score.toFixed(3)})\n${body}`;
      });
      const charged = charge(lines.join("\n\n") || "(no results)", "rag_search");
      if (typeof charged !== "string") return charged;
      return text(charged, { count: items.length, paths: items.map((i) => i.path) });
    },
  };

  const readFile = {
    name: "read_file",
    label: "Read file",
    description: `Read a UTF-8 text file inside the contract's scope.read (path relative to the workspace). Output is truncated to ${maxFileChars} characters.`,
    parameters: Type.Object({
      path: Type.String({ description: "Workspace-relative path, e.g. docs/spec-phase3-6.md" }),
    }),
    async execute(_id: string, params: { path: string }) {
      const check = policy.checkPath(params.path, "read");
      if (check.block) return text(`blocked: ${check.reason}`, { code: check.code }, true);
      const abs = nodePath.resolve(policy.workspace, params.path);
      let body: string;
      try {
        if (statSync(abs).isDirectory()) return text(`not a file: ${params.path}`, {}, true);
        body = readFileSync(abs, "utf8");
      } catch (err) {
        return text(`cannot read ${params.path}: ${(err as Error).message}`, {}, true);
      }
      const truncated = body.length > maxFileChars;
      const charged = charge(truncated ? body.slice(0, maxFileChars) + `\n... (truncated, ${body.length} chars total)` : body, "read_file");
      if (typeof charged !== "string") return charged;
      return text(charged, { chars: body.length, truncated });
    },
  };

  const listFiles = {
    name: "list_files",
    label: "List files",
    description: "List files matching a glob (e.g. docs/**/*.md) inside the contract's scope.read.",
    parameters: Type.Object({
      glob: Type.String({ description: "Glob relative to the workspace, e.g. docs/**/*.md" }),
    }),
    async execute(_id: string, params: { glob: string }) {
      const files = listWorkspaceFiles(policy.workspace, params.glob, maxListEntries).filter(
        (rel) => !policy.checkPath(rel, "read").block,
      );
      const charged = charge(files.join("\n") || "(no files)", "list_files");
      if (typeof charged !== "string") return charged;
      return text(charged, { count: files.length });
    },
  };

  const finish = {
    name: "finish",
    label: "Finish",
    description:
      "Submit the final result. summary: the answer (Markdown allowed). sources: workspace-relative paths of the files the answer is based on. Call this exactly once when done; the harness verifies the result.",
    parameters: Type.Object({
      summary: Type.String({ description: "Final answer" }),
      sources: Type.Array(Type.String(), { description: "Files the answer is based on" }),
    }),
    async execute(_id: string, params: FinishResult) {
      const result: FinishResult = { summary: params.summary, sources: params.sources ?? [] };
      const path = log.writeFile("result.json", result);
      policy.markFinished();
      log.append("finish", {
        summary_chars: result.summary.length,
        sources: result.sources,
        result_path: path,
      });
      return text("result recorded; the harness will verify it", { sources: result.sources });
    },
  };

  return [ragSearch, readFile, listFiles, finish];
}
