# Completion Report: Phase 3(RAG)

| 項目 | 値 |
| --- | --- |
| 契約 | `docs/harness/contracts/phase-3.json`(version 1、`sha256:281acc7d...`) |
| 対象コミット | `e864855`(ブランチ `claude/phase3-4-rag-agent-4c2343`、ベース `df8c706`) |
| 判定 | **needs_human**(`phase-3.decision.json`)。RAG-001〜004 pass、RAG-005 は人のレビュー待ち |
| 作業者 | Claude Code セッション(writer)。検証も同セッションが実行(self-check) |
| 日付 | 2026-10-25 |

## Summary

ai_harness の Markdown 19 本を索引し、質問に近いチャンクを system メッセージに差し込んで `[n]` 付きで答える RAG を、
CLI(`/rag`)、Web UI(トグルと出典リスト)、API(`use_rag` / `top_k`、`sources` イベント、`/api/rag/status`、`/api/rag/search`)に実装した。
埋め込みモデルは `models.ini` に追記した `[Qwen/Qwen3-Embedding-0.6B-GGUF:Q8_0]` から既存のルーターモードで配信できることを実機で確認した。

## Completion Criteria

| ID | 条件(要約) | 方法 | 結果 | 証拠 |
| --- | --- | --- | --- | --- |
| RAG-001 | `llm-index` が documents/chunks を DB に入れ、再実行で未変更ファイルをスキップ | integration-test | **pass** | `tests/rag/test_store_retriever.py::test_index_and_search`(2 回目に `/v1/embeddings` が呼ばれない)。実機: 19 文書 / 433 チャンク、約 20 秒 |
| RAG-002 | 評価 10 問で正解文書が top-3 に 8 問以上 | e2e | **pass** (10/10) | `evidence/phase-3.eval_retrieval.json` |
| RAG-003 | `use_rag=true` で `sources` が本文の前に届き、本文に `[n]` 引用 | integration-test, e2e | **pass** | pytest `test_send_message_with_rag_emits_sources_first`、`test_cli_rag_toggle_and_ask`。実機: `evidence/phase-3.e2e_rag.json`(API 2 問 + CLI 1 問) |
| RAG-004 | 既存テスト込みで `uv run pytest` が全部通る | unit-test | **pass** (70 passed) | `evidence/phase-3.verify.txt` |
| RAG-005 | journal がテンプレート全節を満たし、external/internal-spec に RAG の節 | diff-review, human-review | **not_run**(人のレビュー待ち) | `docs/journal/phase-3.md`、`docs/external-spec.md` 10 章、`docs/internal-spec.md` 11 章 |

`not_run` は `pass` にしない(harness/README 運用ルール 4)。owner がレビューして RAG-005 を pass にすれば decision を `accepted` に更新できる。

## Commands

ベースライン(実装前、`df8c706`):

```
.\scripts\agent\verify.ps1
--> OK: contracts schema / OK: git diff --check / OK: pytest(40 passed in 1.59s)
commit: df8c706
ALL OK
```

実装後(`e864855`、`evidence/phase-3.verify.txt`):

```
.\scripts\agent\verify.ps1
--> OK: contracts schema (phase-3/4/5/6)
--> OK: git diff --check
--> OK: pytest  ......................................................................  70 passed in 3.75s
commit: e864855
ALL OK
```

実機(埋め込み :8082 = 自分で起動したルーター、チャット :8080 = 別セッションが起動済みの Qwen3.6-35B-A3B):

```
$env:LLAMA_BASE_URL = "http://127.0.0.1:8082"; uv run llm-index
  indexed ai_harness/docs/core/decision-model.md (23 chunks)
  ... (19 文書)
更新 19 文書 / 変更なし 0 文書 / 削除 0 文書 / 新規チャンク 433 件

uv run python scripts/agent/eval_retrieval.py --json docs/harness/reports/evidence/phase-3.eval_retrieval.json
index: 19 docs / 433 chunks, model=Qwen/Qwen3-Embedding-0.6B-GGUF:Q8_0
HIT  Q01 rank=2 ... HIT  Q10 rank=1
PASS: 10/10 hit@3 (threshold 8)
```

e2e(`evidence/phase-3.e2e_rag.json` の要点):

| 質問 | 最初のイベント | 出典 top-3 | 本文の引用 | 速度 |
| --- | --- | --- | --- | --- |
| Fencing Token とは何ですか? | `sources` | distributed-execution.md › Fencing Token › 限界 / agent-development-handbook.md › 13.2 / distributed-execution.md › Fencing Token | `[2]` `[3]` | 42.7 tok/s、9.1 s |
| Completion Decision の 4 つの判定 | `sources` | decision-model.md › 8. Completion Decision / agent-development-handbook.md › 7.4 / decision-model.md › 7 Status | `[1]` | 41.3 tok/s、9.4 s |
| (CLI)最小ハーネスの 7 要素 | 出典 3 行 → 本文 | harness-basics.md › 3.5 / 3.1 / 3.2 | `[1]` `[2]` `[3]`、資料に無い部分は「提示できません」と回答 | 42.0 tok/s、9.8 s |
| (対照)`use_rag=false` | `content` | なし | なし(`done.rag` = null) | |

## Files Changed

| 区分 | ファイル |
| --- | --- |
| 追加 | `src/local_llm/rag/{__init__,loader,chunker,embedder,store,retriever,prompt,indexer}.py`、`scripts/fetch-corpus.ps1`、`scripts/agent/eval_retrieval.py`、`tests/rag/*`(5 ファイル + `eval_retrieval.jsonl`)、`docs/journal/phase-3.md`、`docs/harness/reports/evidence/phase-3.*` |
| 変更 | `src/local_llm/client.py`(`embed()`)、`config.py`(埋め込み設定)、`cli.py`(`/rag`)、`web/{app,service,routes_chat}.py`、`web/static/{index.html,app.js,style.css}`、`models.ini`(セクション追記のみ)、`pyproject.toml` / `uv.lock`(numpy、`llm-index`)、`README.md`、`docs/external-spec.md`(10 章)、`docs/internal-spec.md`(11 章) |
| 変更ファイル数 | 35(予算 `max_files_changed` 80 以内) |

すべて `scope.write` の範囲内。`scope.forbidden`(`.env`、`llama-local/**`、`*.gguf`、`data/chat.db`、`.git/**`)には触れていない
(`data/chat.db` は `llm-index` の実行で実行時に更新されるが git 管理外で、契約の「書き込み禁止」はソース変更の意味と解釈した。要確認なら Amendment 候補)。

## Not Run

- RAG-005 の human-review(owner のレビュー)。
- 評価セット(RAG-002)の独立した再評価(別の人が作った質問での測定)。
- Web UI のブラウザ操作による確認(RAG トグル、出典リストの描画)。SSE の内容は API 経由で確認したが、`app.js` の描画は目視していない。
- `scripts/fetch-corpus.ps1` を別 PC で実行する再現確認。

## Residual Risk

| リスク | 深刻度 | 対処 |
| --- | --- | --- |
| 評価セットは実装者が作成(独立性なし) | medium | Phase 6 で第三者の質問を追加して再評価 |
| `[n]` 引用の付与はモデル任せ | low | 引用番号の検出を `done` に含めるのは Phase 6 候補 |
| JSON Schema(`core/schemas/`)は索引外 | low | 必要なら loader に `.json` 対応を追加 |
| numpy 追加(GATE_NEW_DEPENDENCY) | low | 仕様 2 章で明示的に許可済み。owner の確認を求める |

## Amendment 候補(契約の変更は行っていない)

1. `models.ini` の既存セクション `[unsloth/Qwen3-8B-GGUF:Q4_K_M]` に `hf-repo` を足す修正(GATE_MODELS_INI)。Phase 3 で判明した「キャッシュに無いモデルはルーターが `--hf-repo` を付けない」問題の同種で、owner の承認があれば 1 行の追記で直る。
2. `scope.forbidden` の `data/chat.db` の解釈(上記)。

## Rollback

```powershell
git revert e864855          # 実装を戻す(models.ini の追記、numpy も戻る)
Remove-Item data\corpus -Recurse   # 取得したコーパス(git 管理外)
```

索引は `data/chat.db` の `documents` / `chunks` 表に入っている。表を消すだけなら `DROP TABLE chunks; DROP TABLE documents;`。

## 残っているプロセス(人の手で停止が必要)

検証用に起動した llama-server ルーター 2 つの停止操作が実行環境のポリシーで拒否されたため、セッション終了時に残る:
`:8081`(モデル未読み込み)と `:8082`(埋め込みモデルのみ、Phase 4 の検証でも使用)。`:8080` は別セッション(Phase 5)のもので触っていない。
