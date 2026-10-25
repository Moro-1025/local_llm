"""Phase 3: RAG(検索拡張生成)。

- loader   : data/corpus/ の Markdown を読む
- chunker  : 見出し単位で分割し、長い節は約 800 文字(重なり 100 文字)に切る
- embedder : LlamaClient.embed() でベクトル化する(llama-server 以外へは繋がない)
- store    : documents / chunks を SQLite に保存する(埋め込みは float32 の BLOB)
- retriever: コサイン類似度で上位 k 件を返す
- indexer  : コーパス → store を更新する(sha256 が同じ文書は読み飛ばす)
- prompt   : 検索結果を system メッセージに差し込む
"""

from local_llm.rag.embedder import Embedder
from local_llm.rag.retriever import RetrievedChunk, Retriever
from local_llm.rag.store import RagStore

__all__ = ["Embedder", "RagStore", "RetrievedChunk", "Retriever"]
