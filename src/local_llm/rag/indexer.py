"""コーパスを読み、チャンク化・埋め込みして RagStore を更新する。

`uv run llm-index` で実行する。sha256 が前回と同じ文書は読み飛ばし、
コーパスから消えた文書はインデックスからも削除する。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path

from local_llm.client import LlamaClient, LlamaClientError
from local_llm.config import load_settings
from local_llm.rag.chunker import DEFAULT_CHUNK_SIZE, DEFAULT_OVERLAP, chunk_text
from local_llm.rag.embedder import Embedder
from local_llm.rag.loader import load_documents
from local_llm.rag.store import RagStore


@dataclass
class IndexResult:
    indexed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    chunks: int = 0

    def summary(self) -> str:
        return (
            f"更新 {len(self.indexed)} 文書 / 変更なし {len(self.skipped)} 文書 / "
            f"削除 {len(self.deleted)} 文書 / 新規チャンク {self.chunks} 件"
        )


async def index_corpus(
    store: RagStore,
    embedder: Embedder,
    corpus_dir: Path,
    *,
    force: bool = False,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
    log=None,
) -> IndexResult:
    """corpus_dir 配下の Markdown をインデックスする。"""
    store.init_schema()
    result = IndexResult()
    docs = load_documents(corpus_dir)
    seen = {d.path for d in docs}

    for existing in store.list_documents():
        if existing["path"] not in seen:
            store.delete_document(existing["path"])
            result.deleted.append(existing["path"])

    for doc in docs:
        if not force and store.get_document_sha(doc.path) == doc.sha256:
            result.skipped.append(doc.path)
            continue
        chunks = chunk_text(doc.text, chunk_size=chunk_size, overlap=overlap)
        if not chunks:
            result.skipped.append(doc.path)
            continue
        # 見出しを本文に添えて埋め込む(どの文書・節の話か埋め込みに含める)
        texts = [f"{doc.title} › {c.heading}\n{c.text}" if c.heading else f"{doc.title}\n{c.text}" for c in chunks]
        embeddings = await embedder.embed_texts(texts)
        store.replace_document(
            path=doc.path,
            title=doc.title,
            sha256=doc.sha256,
            chunks=chunks,
            embeddings=embeddings,
            embedding_model=embedder.model,
        )
        result.indexed.append(doc.path)
        result.chunks += len(chunks)
        if log:
            log(f"  indexed {doc.path} ({len(chunks)} chunks)")
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="llm-index", description="RAG のインデックスを作成・更新する")
    parser.add_argument("--corpus", type=Path, default=None, help="コーパスのディレクトリ(既定 RAG_CORPUS_DIR)")
    parser.add_argument("--db", type=Path, default=None, help="SQLite のパス(既定 CHAT_DB_PATH)")
    parser.add_argument("--embed-model", default=None, help="埋め込みモデル id(既定 LLM_EMBED_MODEL)")
    parser.add_argument(
        "--base-url", default=None, help="埋め込み用 llama-server の URL(既定 LLAMA_EMBED_BASE_URL、無ければ LLAMA_BASE_URL)"
    )
    parser.add_argument("--force", action="store_true", help="sha256 が同じ文書も作り直す")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--overlap", type=int, default=DEFAULT_OVERLAP)
    return parser.parse_args(argv)


async def _amain(args: argparse.Namespace) -> int:
    settings = load_settings()
    corpus = args.corpus or settings.corpus_dir
    db = args.db or settings.db_path
    model = args.embed_model or settings.embed_model
    base_url = args.base_url or settings.effective_embed_base_url
    print(f"corpus: {corpus}\ndb: {db}\nembed model: {model}\nllama-server: {base_url}")
    async with LlamaClient(base_url, timeout=settings.timeout) as client:
        embedder = Embedder(client, model)
        try:
            result = await index_corpus(
                RagStore(db), embedder, corpus, force=args.force,
                chunk_size=args.chunk_size, overlap=args.overlap, log=print,
            )
        except LlamaClientError as exc:
            print(f"埋め込みに失敗しました: {exc}", file=sys.stderr)
            return 1
    print(result.summary())
    return 0


def main(argv: list[str] | None = None) -> None:
    sys.exit(asyncio.run(_amain(parse_args(argv))))


if __name__ == "__main__":
    main()
