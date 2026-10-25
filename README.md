# local_llm

llama.cpp を使って自宅 PC(RTX 5070 / 12 GB)でローカル LLM を動かし、
API クライアント → チャット UI → RAG → エージェント → ファインチューニングと
段階的に積み上げていく学習・ポートフォリオ用プロジェクト。

> 現在のフェーズ: **Phase 4 完了**(Pi + ハーネスでエージェント、人のレビュー待ち)。次は Phase 6 の評価・公開(Phase 5 は別ブランチで進行中)。Phase 0 のベンチ計測は別途進める

## ハードウェア

| 項目 | 値 |
| --- | --- |
| GPU | NVIDIA GeForce RTX 5070(VRAM 12 GB) |
| CPU | AMD Ryzen 7 7700(8 コア / 16 スレッド) |
| RAM | 64 GB |
| OS | Windows 11 |
| 推論エンジン | llama.cpp build 11146(CUDA 12.4, Windows x64) |

## セットアップ

1. llama.cpp のビルド済みバイナリを取得し、`llama-local/` に展開する
   - https://github.com/ggml-org/llama.cpp/releases から `llama-b11146-bin-win-cuda-12.4-x64.zip`
   - CUDA 12.4 対応の NVIDIA ドライバが必要
2. `models.ini` に使いたいモデルが登録されていることを確認する(下の表)
3. サーバを起動する

```powershell
.\scripts\start-server.ps1            # http://127.0.0.1:8080
.\scripts\start-server.ps1 -Port 8081 # ポート変更
```

ブラウザで http://127.0.0.1:8080 を開くと llama.cpp 内蔵の Web UI が使える。
モデルは `models.ini` のプリセットから選ぶ(ルーターモード)。未ダウンロードのモデルは
初回選択時に Hugging Face から自動取得される(`%USERPROFILE%\.cache\huggingface\hub`)。

API は OpenAI 互換:

```powershell
curl http://127.0.0.1:8080/v1/models
curl http://127.0.0.1:8080/v1/chat/completions -H "Content-Type: application/json" -d '{"model":"unsloth/Qwen3-8B-GGUF:Q4_K_M","messages":[{"role":"user","content":"こんにちは"}]}'
```

## 使い方

### Python 環境

Python 3.12 と [uv](https://docs.astral.sh/uv/) を使う。初回だけ依存を入れる:

```powershell
uv sync
Copy-Item .env.example .env   # 必要なら値を書き換える
```

設定は環境変数または `.env` で行う(`src/local_llm/config.py`):

| 変数 | 既定値 | 内容 |
| --- | --- | --- |
| `LLAMA_BASE_URL` | `http://127.0.0.1:8080` | llama-server の URL |
| `LLM_DEFAULT_MODEL` | `unsloth/Qwen3.6-35B-A3B-GGUF:Q4_K_M` | 既定モデル(`/v1/models` が返す id) |
| `CHAT_DB_PATH` | `data/chat.db` | Web UI の会話 DB(Phase 2) |
| `LLAMA_TIMEOUT` | `600` | HTTP タイムアウト(秒) |
| `LLM_EMBED_MODEL` | `Qwen/Qwen3-Embedding-0.6B-GGUF:Q8_0` | RAG の埋め込みモデル(Phase 3) |
| `LLAMA_EMBED_BASE_URL` | (`LLAMA_BASE_URL` と同じ) | 埋め込みだけ別の llama-server で動かすとき(Phase 3) |
| `RAG_CORPUS_DIR` | `data/corpus` | RAG で索引する Markdown の置き場(Phase 3) |
| `LLM_AGENT_MODEL` | `llama-local/unsloth/Qwen3.6-35B-A3B-GGUF:Q4_K_M` | エージェントが使う Pi の `provider/model-id`(Phase 4、`llm-agent` が読む) |
| `HARNESS_RAG_URL` | `http://127.0.0.1:8000` | エージェントの `rag_search` が叩く `llm-web` の URL(Phase 4、`--rag-url` でも指定可) |

### CLI チャット(Phase 1)

llama-server を起動した状態で:

```powershell
uv run llm-chat                       # 既定モデルで開始
uv run llm-chat --model unsloth/Qwen3.8-27B-GGUF:Q4_K_M
uv run llm-chat --no-think --system "日本語で簡潔に答えて"
```

- 応答はストリーミングで表示される。Qwen3 の thinking(推論過程)は薄い色で本文と区別して出る
- 応答のあとにトークン数と生成速度(tok/s)を 1 行で表示する
- 会話は `logs/chat-<日時>.jsonl` に 1 メッセージ 1 行で保存される

スラッシュコマンド:

| コマンド | 内容 |
| --- | --- |
| `/model` | モデル一覧を表示(`*` が現在のモデル) |
| `/model <id>` | モデルを切り替える |
| `/clear` | 会話履歴を消す |
| `/system <text>` | system prompt を差し替える |
| `/think on\|off` | thinking の有無を切り替える |
| `/rag on\|off` | 資料検索(RAG)の有無を切り替える(Phase 3) |
| `/quit` | 終了(Ctrl+D / Ctrl+Z でも終了) |

### Web チャット UI(Phase 2)

llama-server を起動した状態で:

```powershell
uv run llm-web                 # http://127.0.0.1:8000
uv run llm-web --port 8001     # ポート変更
uv run llm-web --reload        # 開発用(コード変更で自動再起動)
```

ブラウザで http://127.0.0.1:8000 を開く。

- 左のサイドバーに会話一覧。「+ 新規」で新しい会話、各行の「×」で削除
- 上部のドロップダウンでモデルを選ぶ(llama-server の `/v1/models` から取得)。thinking のチェックで推論過程の有無を切り替える
- 応答はストリーミングで逐次描画される。thinking は折りたたみ(本文が始まると自動で閉じる)
- 会話は SQLite(`data/chat.db`)に保存されるので、ブラウザをリロードしても残る
- llama-server が止まっていると、左下の表示が「停止中」になり上部にバナーが出る(15 秒ごとに再確認)
- Enter で送信、Shift+Enter で改行。Markdown は [marked](https://github.com/markedjs/marked)(CDN)で表示

スクリーンショットを撮るときは、サイドバーに 2〜3 件の会話があり、
右側で thinking を開いた応答が 1 つ見えている状態がわかりやすい。

API(すべて `/api` 配下、認証なし・127.0.0.1 前提):

| メソッド | パス | 内容 |
| --- | --- | --- |
| GET | `/api/models` | llama-server の `/v1/models` を中継 |
| GET | `/api/health` | llama-server の疎通(`{"ok": bool}`) |
| GET | `/api/conversations` | 会話一覧(新しい順) |
| POST | `/api/conversations` | 新規作成 `{"model": "..."}` |
| GET | `/api/conversations/{id}` | メッセージ込みで取得 |
| DELETE | `/api/conversations/{id}` | 削除 |
| POST | `/api/conversations/{id}/messages` | `{"content", "enable_thinking", "model"?, "use_rag"?, "top_k"?}` を受け、SSE で応答を流す |
| GET | `/api/rag/status` | RAG 索引の文書数・チャンク数・埋め込みモデル(Phase 3) |
| GET | `/api/rag/search?q=&top_k=` | 質問に近いチャンクを本文つきで返す(Phase 3、Phase 4 のツールが使う) |

SSE のイベントは `data: {"type": "sources"|"reasoning"|"content"|"done"|"error", ...}`。
`done` に usage と tok/s が入る。FastAPI の自動ドキュメントは http://127.0.0.1:8000/docs で見られる。

### RAG: 資料を根拠に答える(Phase 3)

```powershell
.\scripts\fetch-corpus.ps1      # ai_harness の Markdown 19 本を data/corpus/ に取得(初回のみ)
uv run llm-index                 # 索引を作る(llama-server 起動中。埋め込みモデルは初回に約 640 MB を取得)
uv run llm-chat --rag            # CLI で RAG を使う(/rag on|off で切り替え)
uv run llm-web                   # Web UI は上部の「RAG」チェック
```

- 質問に近い資料を上位 k 件(既定 5)選び、「以下の資料だけを根拠に答え、使った資料を [n] で示す」と指示して回答させる
- 出典(パス、見出し、類似度)は本文より先に `sources` イベントで届き、UI では応答の下に並ぶ
- 埋め込みモデルは `models.ini` の `[Qwen/Qwen3-Embedding-0.6B-GGUF:Q8_0]` セクション(`embeddings = true`、`pooling = last`、`hf-repo` 必須)から、同じルーターモードの llama-server が配信する
- 検索精度の評価: `uv run python scripts/agent/eval_retrieval.py`(10 問、top-3 で 10/10)
- 仕組みの説明は [docs/journal/phase-3.md](docs/journal/phase-3.md)

### エージェント: ツール呼び出しをコードで許可する(Phase 4)

[Pi](https://github.com/earendil-works/pi-coding-agent) を RPC モードで子プロセスにし、llama-server に function calling でツールを使わせる。
何を使ってよいか・どこを読んでよいか・いつ止まるかは **Task Contract(JSON)** に書き、Pi の extension(`agent/harness/`)がコードで強制する。

```powershell
npm install -g @earendil-works/pi-coding-agent          # 初回のみ(Node.js が必要)。~/.pi の設定は使わない
cd agent\harness; npm install; npm test; cd ..\..        # extension の依存とテスト(vitest 35 件)
.\scripts\start-server.ps1                               # llama-server(別ターミナル)
uv run llm-web                                            # rag_search が叩く /api/rag/search(別ターミナル、索引済みであること)
uv run llm-agent --contract agent/contracts/summarize-docs.json --task "RAG の設計方針を 3 点にまとめて"
```

- 結果は `evidence/<run_id>/` に `events.jsonl`(全ツール呼び出しと block の記録)、`result.json`(エージェントの finish)、
  `decision.json` / `verification.json` / `report.md`(runner が criterion ごとに検証した判定)。終了コードは判定が `accepted` のときだけ 0
- サンプル Contract `agent/contracts/summarize-docs.json` は読み取り専用: `rag_search` / `read_file` / `list_files` / `finish` だけ許可、`docs/**` と `data/corpus/**` と `README.md` だけ読める
- 確認用オプション: `--max-steps 2`(予算超過で `blocked` になる)、`--expose-builtin-tools`(Pi の bash などを見せても block されることの確認)、`--rag-url`、`--model`
- Pi の設定は `agent/pi/models.json`(`PI_CODING_AGENT_DIR` で渡す)。`contextWindow` を 16384 にしている理由と落とし穴は [docs/journal/phase-4.md](docs/journal/phase-4.md)

### テスト

llama-server は respx でモックするので GPU なしで通る(Web API は FastAPI の TestClient)。エージェントは偽の pi(`tests/agent/fake_pi.py`)で通す:

```powershell
uv run pytest                    # 87 件
.\scripts\agent\verify.ps1 -Agent  # 契約 Schema + diff --check + pytest + agent/harness の vitest
```

### API クライアントを自分のコードから使う

```python
import asyncio
from local_llm import LlamaClient, Message

async def main():
    async with LlamaClient("http://127.0.0.1:8080") as client:
        print(await client.list_models())
        async for chunk in client.chat([Message("user", "こんにちは")], "unsloth/Qwen3.6-35B-A3B-GGUF:Q4_K_M"):
            print(chunk.content, end="", flush=True)

asyncio.run(main())
```

`chunk.reasoning` に thinking、`chunk.usage` に最終チャンクのトークン数が入る。
`enable_thinking=False` を渡すと `chat_template_kwargs` で thinking を無効にする。

## モデル

| プリセット名 | 種類 | サイズ | GPU への載り方 | 用途 |
| --- | --- | --- | --- | --- |
| `unsloth/Qwen3.8-27B-GGUF:UD-Q4_K_M` | dense 27B | 約 15 GB | 39/65 層のみ GPU(残り CPU) | 品質重視・速度は遅い |
| `unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_M` | MoE 35B(アクティブ 3B) | 約 20 GB | エキスパートは CPU、他は GPU(`cpu-moe`) | 速度と品質のバランス |
| `unsloth/Qwen3-8B-GGUF:Q4_K_M` | dense 8B | 約 5 GB | 全層 GPU | 速度重視・比較用 |

## ベンチマーク

計測: `.\scripts\bench.ps1`(結果は `bench/bench-<日時>.md`)。
条件: prompt 512 tok / 生成 128 tok / 3 回平均 / flash-attn on。

| モデル | pp (tok/s) | tg (tok/s) | VRAM (MiB) | メモ |
| --- | --- | --- | --- | --- |
| Qwen3.8-27B UD-Q4_K_M | 143 | 6.0 | 9,740 | llama-server 実測(ngl=39, ctx 8192)。bench.ps1 の値で更新する |
| Qwen3.6-35B-A3B UD-Q4_K_M | - | - | - | 計測待ち |
| Qwen3-8B Q4_K_M | - | - | - | 計測待ち |

### ctx-size と VRAM

計測: `.\scripts\ctx-vram.ps1`(結果は `bench/ctx-vram-<日時>.md`)。

| ctx-size | VRAM (MiB) | メモ |
| --- | --- | --- |
| 8192 | - | 計測待ち |
| 16384 | - | 計測待ち |
| 32768 | - | 計測待ち |

### 普段使いのモデル

計測後にここへ結論を書く(例: 「○○ を既定にする。理由は tg △ tok/s で VRAM に余裕が □ MiB あるため」)。

## フォルダ構成

```
local_llm/
├─ llama-local/      llama.cpp バイナリ(git 管理外)
├─ models.ini        モデルプリセット(llama-server のルーターモード設定)
├─ scripts/
│   ├─ start-server.ps1   サーバ起動
│   ├─ fetch-corpus.ps1   RAG のコーパス(ai_harness の Markdown)を取得
│   ├─ bench.ps1          llama-bench で速度計測
│   └─ ctx-vram.ps1       ctx-size ごとの VRAM 使用量計測
│   └─ agent/             verify.ps1(検証の単一入口)、check_contracts.py、eval_retrieval.py(RAG の精度評価)
├─ agent/            Phase 4 のエージェント
│   ├─ harness/          Pi extension(TypeScript): Contract の強制、証拠の記録、rag_search などのツール、vitest
│   ├─ pi/               Pi の設定(models.json = llama-local プロバイダ、settings.json)。auth.json などの実行時ファイルは git 管理外
│   └─ contracts/        エージェント用 Task Contract(summarize-docs.json)
├─ evidence/         エージェント実行の証拠(run_id ごとに events.jsonl、decision.json、report.md)
├─ bench/            計測結果(Markdown)
├─ docs/             仕様書(初めての人は docs/README.md から)
│   ├─ README.md         読み方ガイドと用語の最低限
│   ├─ external-spec.md   外部仕様(利用者から見た振る舞い、CLI / UI / API)
│   ├─ internal-spec.md   内部仕様(モジュール構成、データフロー、拡張ポイント)
│   ├─ spec-phase1-2.md   Phase 1/2 の決定事項と完了条件
│   ├─ spec-phase3-6.md   Phase 3〜6 の決定事項と完了条件
│   ├─ harness/           開発ハーネス(Task Contract、Completion Report、ai_harness の Schema)
│   └─ journal/           学習ジャーナル(Phase ごとに何をしたか・なぜか・どう確かめるか)
├─ src/local_llm/    Python パッケージ
│   ├─ config.py         設定(環境変数 / .env)
│   ├─ types.py          Message / ChatChunk / Usage
│   ├─ client.py         LlamaClient(httpx、SSE 自前パース、thinking 分離)
│   ├─ history.py        CLI の JSONL ログ
│   ├─ cli.py            CLI チャット(llm-chat)
│   ├─ rag/              RAG(llm-index、loader / chunker / embedder / store / retriever / prompt)
│   ├─ agent/            エージェント runner(llm-agent、contract / scope / runner / verify / report)
│   └─ web/              Web チャット UI(llm-web)
│       ├─ app.py            FastAPI アプリ
│       ├─ db.py             SQLite のスキーマとクエリ
│       ├─ service.py        DB ↔ LlamaClient のオーケストレーション(RAG の差し込みはここ)
│       ├─ routes_chat.py    /api/* のエンドポイント
│       └─ static/           index.html / app.js / style.css(ビルドなし)
├─ tests/            pytest + respx(tests/agent は偽の pi で runner を通す)
├─ data/             会話 DB と RAG 索引、コーパス(git 管理外)
└─ logs/             サーバログ・チャットログ(git 管理外)
```

## ロードマップ

| Phase | 内容 | 状態 |
| --- | --- | --- |
| 0 | 土台づくり(git 化、起動スクリプト、ベンチでモデル選定) | 進行中(ベンチ計測待ち) |
| 1 | API クライアントと CLI チャット | 完了 |
| 2 | Web チャット UI(FastAPI + フロント) | 完了 |
| 3 | RAG(文書を根拠付きで答える) | 完了 |
| 4 | ツール呼び出しとエージェント(Pi + ハーネス) | 完了(人のレビュー待ち) |
| 5 | LoRA ファインチューニング | 進行中 |
| 6 | 評価・計測・公開 | |

## 学んだこと

- 27B dense の Q4_K_M は約 15 GB あり、12 GB VRAM には収まらない。llama-server は空き VRAM に合わせて GPU 層数を自動調整する(今回は 39/65 層)が、CPU 側に残った層がボトルネックになり生成は約 6 tok/s にとどまる
- llama-server のルーターモード(`--models-preset`)は、`models.ini` の `unsloth/...:UD-Q4_K_M` を `unsloth/...:Q4_K_M` という id で公開する(`UD-` を落とす)。Hugging Face の manifest API では両タグが同じ GGUF に解決されるので実体は同じ。API に渡す id は `/v1/models` で確認する
- llama-server は `--reasoning-format` の既定で Qwen3 の thinking を `reasoning_content` フィールドに分離して返す。ただし本文に `<think>` が混ざる場合もあるので、クライアント側でタグのフォールバック分離も持たせた(タグがチャンク境界で分断されることを考慮する必要がある)
- SSE は「`data:` 行が空行で区切られる」だけの単純な形式で、`[DONE]` が終端。httpx の `aiter_lines()` で十分にパースできる
- FastAPI の `StreamingResponse` に async generator を渡すと、ブラウザが切断したときに generator が閉じられる(`GeneratorExit` / `CancelledError`)。`finally` でそこまでの本文を保存すれば「途中まで」を失わない
- 過去ターンの thinking は llama-server に送らない(Qwen3 の推奨)。DB には残すので UI では見返せる
- llama-server のルーターモードは、Hugging Face のキャッシュに無いモデルには `--hf-repo` を自動で付けない。`models.ini` のセクションに `hf-repo = <repo:tag>` を明示しないと、子プロセスがモデル無しで起動して `loading` のまま戻らない(Phase 3 で判明)
- RAG の検索は 433 チャンク程度ならベクトル DB なしで足りる。numpy の行列積 1 回(433 × 1024)で全件のコサイン類似度が出る
- Pi は `max_tokens = min(maxTokens, contextWindow − 推定使用量 − 4096)` で出力長を切り詰める。`contextWindow` を実サーバと同じ 8192 にすると、使用量 4k で max_tokens が 1 になりモデルが何も言えなくなる。`models.json` では 16384 と申告し、ハーネス側のツール出力予算で実コンテキストを 8k 以内に抑えた(Phase 4 で判明)
- Pi の `--thinking off` は llama-server には伝わらない。`before_provider_request` フックでリクエストに `chat_template_kwargs.enable_thinking=false` を足すと Qwen3 の thinking が止まる
- 「モデルは提案し、コードが許可する」は小さく実装できる。Pi の `tool_call` フックで `{block: true}` を返すだけで許可外ツールは実行されず、記録も残る。system prompt の「使わないで」は不要ではないが、それだけに頼らない
- (ベンチ後に追記)
