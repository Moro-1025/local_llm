"""docs/ と ai_harness の Markdown から、llama-server で Q&A を合成して qa.jsonl を作る(Phase 5 / LORA-002)。

使い方(リポジトリのルートで。Windows 側の uv 環境でも WSL の venv でも動く。依存は httpx だけ):

    uv run python training/data/build_dataset.py                 # 全チャンク
    uv run python training/data/build_dataset.py --limit 5       # 試しに 5 チャンク
    uv run python training/data/build_dataset.py --base-url http://172.25.80.1:8080   # WSL から(-Expose 起動時)

流れ:
    1. README.md、docs/**/*.md、data/corpus/ai_harness/**/*.md を見出し単位でチャンクにする(決定論的)
    2. 各チャンクを llama-server(OpenAI 互換 /v1/chat/completions、JSON Schema 指定)に渡し、
       「このチャンクだけから答えられる Q&A を 3 組」作らせる。応答は data/cache/ にキャッシュ(再実行で再利用)
    3. 決定論的に除外: 重複質問、答え 20 字未満、質問 10 字未満、質問にチャンク内の語(出典語)が無いもの
    4. training/data/qa.jsonl({"question","answer","source"})と qa.meta.json(プロンプト、モデル id、日時、件数)を書く
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(__file__).resolve().parent
CACHE_DIR = DATA_DIR / "cache"

DEFAULT_MODEL = "unsloth/Qwen3.6-35B-A3B-GGUF:Q4_K_M"
DEFAULT_BASE_URL = os.environ.get("LLAMA_BASE_URL", "http://127.0.0.1:8080")

# 学習データに入れないファイル(Phase 5 自身の記録。評価汚染と自己参照を避ける)
EXCLUDE_PATTERNS = ("docs/journal/phase-5", "docs/harness/reports/phase-5")

PROMPT_VERSION = "v1"
SYSTEM_PROMPT = (
    "あなたは技術文書から学習用の Q&A データを作る係です。"
    "与えられた文章だけを根拠にして、日本語で質問と答えを作ります。文章に無いことは書きません。"
)
USER_PROMPT_TEMPLATE = """次の文章だけから答えられる「質問と答え」を 3 組作ってください。

条件:
- 質問には、文章に出てくる固有の語(ファイル名、コマンド、設定名、用語など)を必ず 1 つ以上含める
- 答えは 30 字以上 200 字以内。文章に書かれていることだけを、自然な日本語の文で書く
- 3 組は互いに違う内容を問う
- 出力は JSON 配列のみ: [{{"question": "...", "answer": "..."}}, ...]

出典: {source}

文章:
<<<
{chunk}
>>>"""

RESPONSE_SCHEMA = {
    "type": "array",
    "minItems": 3,
    "maxItems": 3,
    "items": {
        "type": "object",
        "properties": {
            "question": {"type": "string"},
            "answer": {"type": "string"},
        },
        "required": ["question", "answer"],
    },
}

MIN_ANSWER_CHARS = 20
MIN_QUESTION_CHARS = 10


# ---------------------------------------------------------------- チャンク化
@dataclass(frozen=True)
class Chunk:
    source: str  # "docs/external-spec.md#見出し"
    text: str


def iter_source_files(root: Path) -> list[Path]:
    files = [root / "README.md"]
    files += sorted(root.glob("docs/**/*.md"))
    files += sorted(root.glob("data/corpus/ai_harness/**/*.md"))
    out = []
    for f in files:
        if not f.is_file():
            continue
        rel = f.relative_to(root).as_posix()
        if any(rel.startswith(p) for p in EXCLUDE_PATTERNS):
            continue
        out.append(f)
    return out


_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*$", re.M)


def split_sections(text: str) -> list[tuple[str, str]]:
    """見出し(#〜###)ごとに (見出し, 本文) に分ける。先頭に見出しが無い部分は ("", 本文)。"""
    sections: list[tuple[str, str]] = []
    pos = 0
    title = ""
    for m in _HEADING.finditer(text):
        body = text[pos : m.start()]
        if body.strip():
            sections.append((title, body.strip()))
        title = m.group(2)
        pos = m.end()
    body = text[pos:]
    if body.strip():
        sections.append((title, body.strip()))
    return sections


def _split_long(text: str, max_chars: int) -> list[str]:
    """段落の境界で max_chars 以下に分ける。1 段落が長すぎれば行で切る。"""
    parts: list[str] = []
    buf = ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if len(para) > max_chars:  # 巨大な段落(表やコードブロック)は行で切る
            for line in para.splitlines():
                if len(buf) + len(line) + 1 > max_chars and buf:
                    parts.append(buf)
                    buf = ""
                buf += ("\n" if buf else "") + line
            continue
        if len(buf) + len(para) + 2 > max_chars and buf:
            parts.append(buf)
            buf = ""
        buf += ("\n\n" if buf else "") + para
    if buf:
        parts.append(buf)
    return parts


def chunk_markdown(rel_path: str, text: str, *, min_chars: int, max_chars: int) -> list[Chunk]:
    """見出し単位 → 短すぎる節は次と結合、長すぎる節は段落で分割。結果は決定論的。"""
    chunks: list[Chunk] = []
    pending_title = ""
    pending = ""
    for title, body in split_sections(text):
        label = title or "(冒頭)"
        combined = (pending + "\n\n" if pending else "") + (f"## {title}\n" if title else "") + body
        pending_title = pending_title or label
        if len(combined) < min_chars:
            pending = combined
            continue
        for i, part in enumerate(_split_long(combined, max_chars)):
            suffix = f" ({i + 1})" if i else ""
            chunks.append(Chunk(source=f"{rel_path}#{pending_title}{suffix}", text=part))
        pending, pending_title = "", ""
    if pending and len(pending) >= min_chars // 2:
        chunks.append(Chunk(source=f"{rel_path}#{pending_title}", text=pending))
    return chunks


def build_chunks(root: Path, *, min_chars: int, max_chars: int) -> list[Chunk]:
    chunks: list[Chunk] = []
    for f in iter_source_files(root):
        rel = f.relative_to(root).as_posix()
        chunks += chunk_markdown(rel, f.read_text(encoding="utf-8"), min_chars=min_chars, max_chars=max_chars)
    return chunks


# ---------------------------------------------------------------- フィルタ(決定論的)
_ASCII_TERM = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./\-]{2,}")
_CJK_TERM = re.compile(r"[゠-ヿ一-鿿々]{2,}")
_NORMALIZE = re.compile(r"[\s\W_]+", re.UNICODE)


def normalize_question(q: str) -> str:
    return _NORMALIZE.sub("", q).lower()


def question_terms(q: str) -> list[str]:
    """質問から「出典語」候補を取り出す。英数字 3 字以上の語と、カタカナ/漢字 2 字以上の連続。"""
    return _ASCII_TERM.findall(q) + _CJK_TERM.findall(q)


def has_source_term(question: str, chunk_text: str) -> bool:
    """質問の出典語候補のうち 1 つ以上がチャンク本文に出ていれば True。"""
    low = chunk_text.lower()
    return any(t.lower() in low for t in question_terms(question))


def filter_pairs(pairs: list[dict]) -> tuple[list[dict], dict[str, int]]:
    """pairs: {"question","answer","source","chunk_text"} の列。除外理由ごとの件数も返す。"""
    kept: list[dict] = []
    seen: set[str] = set()
    reasons = {"short_answer": 0, "short_question": 0, "no_source_term": 0, "duplicate": 0}
    for p in pairs:
        q = " ".join(str(p["question"]).split())
        a = str(p["answer"]).strip()
        if len(a) < MIN_ANSWER_CHARS:
            reasons["short_answer"] += 1
            continue
        if len(q) < MIN_QUESTION_CHARS:
            reasons["short_question"] += 1
            continue
        if not has_source_term(q, p["chunk_text"]):
            reasons["no_source_term"] += 1
            continue
        key = normalize_question(q)
        if key in seen:
            reasons["duplicate"] += 1
            continue
        seen.add(key)
        kept.append({"question": q, "answer": a, "source": p["source"]})
    return kept, reasons


# ---------------------------------------------------------------- 合成(llama-server)
def cache_key(model: str, chunk: Chunk) -> str:
    h = hashlib.sha1()
    h.update(f"{PROMPT_VERSION}|{model}|{chunk.source}|".encode("utf-8"))
    h.update(chunk.text.encode("utf-8"))
    return h.hexdigest()


def synthesize(client, base_url: str, model: str, chunk: Chunk, *, temperature: float) -> list[dict]:
    """1 チャンクに対して Q&A の配列を返す。JSON Schema で形を強制する。"""
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_PROMPT_TEMPLATE.format(source=chunk.source, chunk=chunk.text)},
        ],
        "temperature": temperature,
        "max_tokens": 1024,
        "response_format": {"type": "json_schema", "json_schema": {"name": "qa", "schema": RESPONSE_SCHEMA}},
        "chat_template_kwargs": {"enable_thinking": False},
    }
    r = client.post(f"{base_url}/v1/chat/completions", json=body, timeout=600)
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
    data = json.loads(content)
    if not isinstance(data, list):
        raise ValueError(f"配列ではない応答: {content[:100]}")
    return [d for d in data if isinstance(d, dict) and "question" in d and "answer" in d]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--out", default=str(DATA_DIR / "qa.jsonl"))
    ap.add_argument("--limit", type=int, default=0, help="チャンク数の上限(0 = 全部)")
    ap.add_argument("--min-chunk-chars", type=int, default=400)
    ap.add_argument("--max-chunk-chars", type=int, default=1800)
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--dry-run", action="store_true", help="チャンク化だけして件数を表示")
    args = ap.parse_args(argv)

    chunks = build_chunks(ROOT, min_chars=args.min_chunk_chars, max_chars=args.max_chunk_chars)
    if args.limit:
        chunks = chunks[: args.limit]
    files = {c.source.split("#")[0] for c in chunks}
    print(f"chunks: {len(chunks)} from {len(files)} files  (chars: {sum(len(c.text) for c in chunks):,})")
    if args.dry_run:
        for c in chunks[:10]:
            print(f"  {len(c.text):5d}  {c.source}")
        return 0

    import httpx  # Windows の uv 環境にも WSL の venv にも入っている

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc)
    raw: list[dict] = []
    errors = 0
    with httpx.Client() as client:
        models = client.get(f"{args.base_url}/v1/models", timeout=30).json()
        ids = [m["id"] for m in models.get("data", [])]
        if args.model not in ids:
            print(f"モデル {args.model} が無い。/v1/models: {ids}", file=sys.stderr)
            return 1
        t0 = time.time()
        for i, chunk in enumerate(chunks, 1):
            cpath = CACHE_DIR / f"{cache_key(args.model, chunk)}.json"
            if cpath.exists():
                pairs = json.loads(cpath.read_text(encoding="utf-8"))
            else:
                try:
                    pairs = synthesize(client, args.base_url, args.model, chunk, temperature=args.temperature)
                except Exception as e:  # noqa: BLE001 - 1 チャンクの失敗で全体を止めない
                    errors += 1
                    print(f"[{i}/{len(chunks)}] NG {chunk.source}: {type(e).__name__}: {str(e)[:120]}", flush=True)
                    if errors >= 3 and errors == i:  # 最初から連続で失敗 → 設定ミスなので止める
                        print("連続して失敗したので中断", file=sys.stderr)
                        return 1
                    continue
                cpath.write_text(json.dumps(pairs, ensure_ascii=False), encoding="utf-8")
            for p in pairs:
                raw.append({**p, "source": chunk.source, "chunk_text": chunk.text})
            if i % 10 == 0 or i == len(chunks):
                el = time.time() - t0
                print(f"[{i}/{len(chunks)}] raw={len(raw)} errors={errors} {el:.0f}s", flush=True)

    kept, reasons = filter_pairs(raw)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for p in kept:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    meta = {
        "dataset": "qa.jsonl",
        "prompt_version": PROMPT_VERSION,
        "system_prompt": SYSTEM_PROMPT,
        "user_prompt_template": USER_PROMPT_TEMPLATE,
        "response_schema": RESPONSE_SCHEMA,
        "model": args.model,
        "base_url": args.base_url,
        "temperature": args.temperature,
        "enable_thinking": False,
        "started_at": started.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "sources": sorted(files),
        "chunking": {"min_chars": args.min_chunk_chars, "max_chars": args.max_chunk_chars, "chunks": len(chunks)},
        "counts": {"raw": len(raw), "kept": len(kept), "chunk_errors": errors, "dropped": reasons},
        "filters": {
            "min_answer_chars": MIN_ANSWER_CHARS,
            "min_question_chars": MIN_QUESTION_CHARS,
            "source_term": "質問中の英数字 3 字以上の語、またはカタカナ/漢字 2 字以上の連続が、チャンク本文に含まれること",
        },
    }
    (out.parent / "qa.meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"kept {len(kept)} / raw {len(raw)}  dropped={reasons}  -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
