# local_llm

llama.cpp を使って自宅 PC(RTX 5070 / 12 GB)でローカル LLM を動かし、
API クライアント → チャット UI → RAG → エージェント → LoRA ファインチューニング → 評価・計測と
段階的に積み上げた学習・ポートフォリオ用プロジェクト。

> 現在のフェーズ: **Phase 6 完了**(評価・計測・公開準備)。Phase 0〜6 のロードマップは [下](#ロードマップ)。
> 開発は [ai_harness](https://github.com/Moro-1025/ai_harness) の考え方(契約 → 実装 → 同じコマンドで検証 → 証拠付きの Completion Report)で進めた。
> 各 Phase の「何を・なぜ・どう確かめたか」は [docs/journal/](docs/journal/README.md)、判定は [docs/harness/reports/](docs/harness/README.md)。

## 構成図

```mermaid
flowchart LR
    user([利用者])
    subgraph clients["クライアント(uv run ...)"]
        cli["llm-chat<br>CLI(Phase 1)"]
        web["llm-web<br>FastAPI + ブラウザ(Phase 2)"]
        agent["llm-agent<br>Pi + ハーネス(Phase 4)"]
        evalc["llm-eval<br>5 構成の評価(Phase 6)"]
    end
    subgraph server["llama-server(ルーターモード、127.0.0.1:8080)"]
        big["Qwen3.6-35B-A3B<br>(MoE、cpu-moe)"]
        small["Qwen3-4B / 4B+LoRA<br>(Phase 5 で学習)"]
        grader["Qwen3-8B<br>(採点用)"]
        emb["Qwen3-Embedding-0.6B<br>(/v1/embeddings)"]
    end
    db[("SQLite data/chat.db<br>会話 + RAG 索引")]
    corpus["data/corpus/<br>docs/ + ai_harness の Markdown"]
    user --> cli & web
    cli & web -->|OpenAI 互換 API| big
    cli & web -->|RAG(Phase 3)| emb
    web --> db
    corpus -->|llm-index| db
    agent -->|function calling| big
    agent -->|rag_search| web
    evalc --> big & small & grader & emb
    evalc --> results["eval/results/*.json"]
```

## ハードウェア

| 項目 | 値 |
| --- | --- |
| GPU | NVIDIA GeForce RTX 5070(VRAM 12 GB) |
| CPU | AMD Ryzen 7 7700(8 コア / 16 スレッド) |
| RAM | 64 GB |
| OS | Windows 11(LoRA 学習だけ WSL2 Ubuntu 24.04) |
| 推論エンジン | llama.cpp build 11146(CUDA 12.4, Windows x64) |

## 各 Phase の成果

| Phase | 成果 | 数値・証拠 | 詳しく |
| --- | --- | --- | --- |
| 0 | `llama-server` のルーターモードで 3 モデルを切り替える土台、計測スクリプト | [ベンチマーク](#ベンチマーク)の表 | [journal](docs/journal/phase-0-2.md) |
| 1 | httpx で OpenAI 互換 API を叩く `LlamaClient`(SSE 自前パース、thinking 分離)と CLI `llm-chat` | pytest 40 件(当時) | [journal](docs/journal/phase-0-2.md) |
| 2 | FastAPI + 素の HTML/JS の Web UI `llm-web`、会話を SQLite に保存、SSE で逐次描画 | 同上 | [journal](docs/journal/phase-0-2.md) |
| 3 | RAG: Markdown を見出し単位でチャンク化、`/v1/embeddings` で埋め込み、numpy 総当たりで検索、`[n]` 引用 | 検索精度 10 問 top-3 で 10/10 | [journal](docs/journal/phase-3.md) / [report](docs/harness/reports/phase-3.md) |
| 4 | Pi を RPC モードで子プロセスにし、Task Contract をコードで強制する読み取り専用エージェント `llm-agent` | vitest 35 件、実機 4 run(accepted 2 / blocked 2) | [journal](docs/journal/phase-4.md) / [report](docs/harness/reports/phase-4.md) |
| 5 | WSL + unsloth で Qwen3-4B に QLoRA(1,324 組の合成 Q&A)、GGUF に変換して `models.ini` に登録 | eval loss 3.91 → 1.68、学習 290 秒、VRAM ピーク 5.9 GiB | [journal](docs/journal/phase-5.md) / [report](docs/harness/reports/phase-5.md) |
| 6 | 評価ハーネス `llm-eval`(5 構成 × 147 問、決定論 + LLM の 2 段採点)、bench / ctx-vram の計測、README と LICENSE | [評価結果](#評価5-構成の比較phase-6)と[ベンチマーク](#ベンチマーク) | [journal](docs/journal/phase-6.md) / [report](docs/harness/reports/phase-6.md) |

## 再現手順(最短)

```powershell
# 0. llama.cpp のバイナリ(llama-b11146-bin-win-cuda-12.4-x64.zip)を llama-local/ に展開しておく
uv sync                                  # Python 3.12 + uv。依存を入れる
Copy-Item .env.example .env              # 必要なら値を書き換える
.\scripts\start-server.ps1               # llama-server(ルーターモード)。http://127.0.0.1:8080
uv run llm-chat                          # CLI チャット
uv run llm-web                           # Web UI。http://127.0.0.1:8000
.\scripts\fetch-corpus.ps1; .\scripts\sync-local-corpus.ps1; uv run llm-index   # RAG の索引
uv run llm-chat --rag                    # RAG 付き CLI(Web UI は「RAG」チェック)
uv run llm-agent --contract agent/contracts/summarize-docs.json --task "RAG の設計方針を 3 点にまとめて"   # エージェント(Pi と llm-web が必要)
uv run llm-eval --suite qa --config A    # 評価(構成 A〜E。詳細は下)
uv run pytest                            # テスト(GPU 不要、llama-server はモック)
```

モデルは `models.ini` の登録済みプリセットから選び、未ダウンロードのものは初回選択時に Hugging Face から取得される
(`%USERPROFILE%\.cache\huggingface\hub`)。LoRA モデルだけは `training/` の手順で生成した GGUF が
`C:\dev\local_llm\models\` にある前提(`models.ini` の `[local/qwen3-4b-localllm-lora]`)。

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
モデルは `models.ini` のプリセットから選ぶ(ルーターモード)。

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
| `LLM_GRADER_MODEL` | `unsloth/Qwen3-8B-GGUF:Q4_K_M` | 評価の採点モデル(Phase 6、回答モデルと別であること) |

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
.\scripts\fetch-corpus.ps1       # ai_harness の Markdown を data/corpus/ai_harness/ に取得(初回のみ)
.\scripts\sync-local-corpus.ps1  # このリポジトリの README と docs/ を data/corpus/local_llm/ にコピー(Phase 6 で追加)
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

### LoRA ファインチューニング(Phase 5)

WSL Ubuntu + unsloth で Qwen3-4B に QLoRA をかけ、GGUF に変換して `models.ini` に `local/qwen3-4b-localllm-lora` として登録した。
手順と数値は [training/README.md](training/README.md) と [docs/journal/phase-5.md](docs/journal/phase-5.md)。
Hugging Face 用のモデルカード下書きは [training/export/README.md](training/export/README.md)(アップロードは人間が行う)。

### 評価: 5 構成の比較(Phase 6)

同じ 147 問(`eval/datasets/qa-v1.jsonl`、Phase 5 の学習に使っていない eval 側)を 5 構成に投げ、
決定論的採点(正解文のキーワード再現率 ≥ 0.5 で正答)と LLM 採点(採点モデル Qwen3-8B、1〜5)で比べる。
採点モデルは回答モデルと別で、結果 JSON に両方の id が記録され、`eval/schemas/qa-result.schema.json` と
`scripts/agent/check_eval_results.py` で検証する。

```powershell
.\scripts\start-server.ps1 -Preset .\eval\models-eval.ini   # 採点モデル(8B)に hf-repo を足したプリセット(理由は docs/external-spec.md 12.6)
uv run llm-index                                             # RAG あり構成(B / E)の索引(上の RAG の手順)
uv run llm-eval --suite qa --config A                        # 1 構成。結果は eval/results/<日時>-qa-A.{json,md}
.\scripts\run-eval-all.ps1                                   # A〜E を順に実行し、最後に比較表を出す
uv run llm-eval --summarize                                  # 構成ごと最新の結果から比較表を出す(下の表はこの出力)
```

<!-- eval-table:start -->
| 構成 | モデル | RAG | 正答率(決定論) | キーワード再現率 | LLM 採点(1〜5) | 引用率 | 検索ヒット top-3 | tok/s | 結果ファイル |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A | `unsloth/Qwen3.6-35B-A3B-GGUF:Q4_K_M` | なし | 53 / 147(36.0%) | 0.410 | 2.72 | 0.0% | - | 36.9 | `20261025-171641-qa-A.json` |
| B | `unsloth/Qwen3.6-35B-A3B-GGUF:Q4_K_M` | あり | 79 / 147(53.7%) | 0.536 | 3.82 | 91.2% | 77.5% | 16.7 | `20261025-173634-qa-B.json` |
| C | `unsloth/Qwen3-4B-GGUF:Q4_K_M` | なし | 27 / 147(18.4%) | 0.326 | 2.55 | 0.0% | - | 153.6 | `20261025-174748-qa-C.json` |
| D | `local/qwen3-4b-localllm-lora` | なし | 32 / 147(21.8%) | 0.346 | 2.58 | 0.0% | - | 152.5 | `20261025-175007-qa-D.json` |
| E | `local/qwen3-4b-localllm-lora` | あり | 98 / 147(66.7%) | 0.599 | 3.77 | 55.1% | 77.5% | 117.4 | `20261025-175230-qa-E.json` |

- 採点モデルは全構成で `unsloth/Qwen3-8B-GGUF:Q4_K_M`(回答モデルと別。LLM 採点の失敗 0 / 735 件)。実行 2026-10-25 08:16〜08:55 UTC、結果は `eval/results/`
- 要点: RAG の効果が最も大きい(A→B で +17.7 pt、D→E で +44.9 pt)。LoRA 単体の効果は小さい(C→D で +3.4 pt、LLM 採点 2.55→2.58)。LoRA + RAG の E が決定論の正答率で最も高く、LLM 採点では B(35B + RAG)と同程度
- A は 147 問中 33 問が max_tokens 400 で打ち切り(平均 278 トークン生成)。RAG あり構成は 70〜80 トークンで答えるので打ち切りは 1 問
<!-- eval-table:end -->

- 条件: 147 問全件(seed 42 は部分集合用で未使用)、temperature 0、thinking off、max_tokens 400、RAG は top_k 5。採点は温度 0、JSON Schema 強制
- 読み方と限界(件数、LLM 採点の主観性、データセットが学習データと同じ合成元であること)は [docs/journal/phase-6.md](docs/journal/phase-6.md)

### テスト

llama-server は respx でモックするので GPU なしで通る(Web API は FastAPI の TestClient)。エージェントは偽の pi(`tests/agent/fake_pi.py`)、評価は偽のルーター(`tests/eval/conftest.py`)で通す:

```powershell
uv run pytest                    # 107 件
.\scripts\agent\verify.ps1 -Agent  # 契約 Schema + 結果 JSON の Schema + diff --check + pytest + agent/harness の vitest
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
| `unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_M` | MoE 35B(アクティブ 3B) | 約 20 GB | エキスパートは CPU、他は GPU(`cpu-moe`) | 速度と品質のバランス。既定モデル |
| `unsloth/Qwen3-8B-GGUF:Q4_K_M` | dense 8B | 約 5 GB | 全層 GPU | 速度重視・比較用、Phase 6 の採点モデル |
| `unsloth/Qwen3-4B-GGUF:Q4_K_M` | dense 4B | 約 2.5 GB | 全層 GPU | LoRA との比較用(Phase 5) |
| `local/qwen3-4b-localllm-lora` | dense 4B + LoRA マージ | 約 2.5 GB | 全層 GPU | docs/ と ai_harness の Q&A で学習(Phase 5、`training/export_gguf.sh` で生成) |
| `Qwen/Qwen3-Embedding-0.6B-GGUF:Q8_0` | 埋め込み 0.6B | 約 0.6 GB | 全層 GPU | RAG の埋め込み(Phase 3) |

## ベンチマーク

計測: `.\scripts\bench.ps1`(結果は `bench/bench-<日時>.md`)。
条件: prompt 512 tok / 生成 128 tok / 3 回平均 / flash-attn on。

<!-- bench-table:start -->
| モデル | 種類 | GGUF サイズ | GPU 層数 | pp512(tok/s) | tg128(tok/s) |
| --- | --- | --- | --- | --- | --- |
| `unsloth/Qwen3-4B-GGUF:Q4_K_M` | dense 4B | 2.32 GiB | 99(全層) | 8318 ± 341 | 180.1 ± 1.2 |
| `local/qwen3-4b-localllm-lora`(Phase 5 の LoRA マージ GGUF) | dense 4B | 2.32 GiB | 99(全層) | 8317 ± 334 | 179.7 ± 1.1 |
| `unsloth/Qwen3-8B-GGUF:Q4_K_M` | dense 8B | 4.68 GiB | 99(全層) | 5020 ± 119 | 113.7 ± 0.5 |
| `unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_M` | MoE 35B(アクティブ 3B) | 20.60 GiB | 99 + エキスパートは CPU(`-ot`) | 494 ± 73 | 43.5 ± 0.7 |
| `unsloth/Qwen3.8-27B-GGUF:UD-Q4_K_M` | dense 27B | 15.32 GiB | 39/65 層 | 99.2 ± 0.9 | 5.9 ± 0.04 |

- 計測日 2026-10-25、RTX 5070 12 GB、llama.cpp b11146。生の出力は `bench/bench-20261025-175710.md`
- 計測中は前のセッションの llama-server(:8080)と llm-web が残っていた(VRAM 約 1.7〜3.4 GB を占有)。27B は 39 層でも VRAM に収まり、OOM は起きていない
- LoRA マージ GGUF は素の 4B と同じ速度(構造が同じなので当然だが、マージで遅くなっていないことの確認)
<!-- bench-table:end -->

### ctx-size と VRAM

計測: `.\scripts\ctx-vram.ps1`(結果は `bench/ctx-vram-<日時>.md`)。

<!-- ctx-table:start -->
| ctx-size | VRAM 使用(MiB) | 起動前比 | 読み込み秒 |
| --- | --- | --- | --- |
| 8192 | 5644 | +3906 | 31 |
| 16384 | 5820 | +4082 | 8 |
| 32768 | 6296 | +4558 | 8 |

- モデル `unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_M`(`--cpu-moe`)、起動前の VRAM 使用 1738 MiB(前のセッションのプロセス分)。生の出力は `bench/ctx-vram-20261025-175920.md`
- MoE のエキスパートを CPU に置いているので、GPU 側は注意層 + KV キャッシュだけ。ctx を 8k → 32k にしても +650 MiB で、12 GB の GPU なら 32k まで余裕がある。初回 31 秒はファイルキャッシュが無い状態の読み込み、2 回目以降は 8 秒
<!-- ctx-table:end -->

### 普段使いのモデル

<!-- daily-model:start -->
- **普段使いは `unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_M`(既定)**。生成 43.5 tok/s(llama-bench)/ 実運用 37 tok/s(評価 A)で読む速さを超え、素の状態でも 4B 系より正答率が高く(36.0% 対 18.4%)、RAG を付けると 53.7%。VRAM は ctx 32k でも約 4.6 GB
- **27B は品質が欲しいときだけ**。39 層しか GPU に載らず 5.9 tok/s で、会話には遅すぎる
- **8B は採点用**。113.7 tok/s で、Phase 6 では回答モデルと別の採点モデルとして使った
- **4B LoRA は RAG と組み合わせたときだけ意味がある**。単体では 21.8%(素の 4B と 3 pt 差)だが、RAG を付けると 66.7% で最も高く、180 tok/s と速い。短く答える代わりに引用率が 55% と低い(B は 91%)
<!-- daily-model:end -->

## フォルダ構成

```
local_llm/
├─ llama-local/      llama.cpp バイナリ(git 管理外)
├─ models.ini        モデルプリセット(llama-server のルーターモード設定)
├─ LICENSE           MIT
├─ scripts/
│   ├─ start-server.ps1       サーバ起動
│   ├─ fetch-corpus.ps1       RAG のコーパス(ai_harness の Markdown)を取得
│   ├─ sync-local-corpus.ps1  README と docs/ を RAG のコーパスにコピー(Phase 6)
│   ├─ run-eval-all.ps1       評価 5 構成を順に実行(Phase 6)
│   ├─ bench.ps1              llama-bench で速度計測
│   ├─ ctx-vram.ps1           ctx-size ごとの VRAM 使用量計測
│   └─ agent/                 verify.ps1(検証の単一入口)、check_contracts.py、check_eval_results.py、eval_retrieval.py
├─ agent/            Phase 4 のエージェント
│   ├─ harness/          Pi extension(TypeScript): Contract の強制、証拠の記録、rag_search などのツール、vitest
│   ├─ pi/               Pi の設定(models.json = llama-local プロバイダ、settings.json)
│   └─ contracts/        エージェント用 Task Contract(summarize-docs.json)
├─ evidence/         エージェント実行の証拠(run_id ごとに events.jsonl、decision.json、report.md)
├─ eval/             Phase 6 の評価
│   ├─ datasets/         評価用 Q&A(版番号付き、学習には使わない)
│   ├─ schemas/          結果 JSON の Schema
│   ├─ results/          評価結果(JSON + Markdown)
│   └─ models-eval.ini   評価用のモデルプリセット(models.ini + 採点モデルの hf-repo)
├─ bench/            計測結果(Markdown)
├─ training/         Phase 5: LoRA 学習(WSL で実行。データ合成・学習・GGUF 変換)
├─ docs/             仕様書とジャーナル(初めての人は docs/README.md から)
│   ├─ external-spec.md   外部仕様(利用者から見た振る舞い、CLI / UI / API / RAG / エージェント / 評価)
│   ├─ internal-spec.md   内部仕様(モジュール構成、データフロー)
│   ├─ spec-phase1-2.md / spec-phase3-6.md   決定事項と完了条件
│   ├─ harness/           開発ハーネス(Task Contract、Completion Report、ai_harness の Schema)
│   └─ journal/           学習ジャーナル(Phase ごとに何をしたか・なぜか・どう確かめるか)
├─ src/local_llm/    Python パッケージ
│   ├─ config.py / types.py / client.py / history.py / cli.py
│   ├─ rag/              RAG(llm-index、loader / chunker / embedder / store / retriever / prompt)
│   ├─ agent/            エージェント runner(llm-agent)
│   ├─ eval/             評価ハーネス(llm-eval、configs / dataset / graders / runner / report)
│   └─ web/              Web チャット UI(llm-web)
├─ tests/            pytest + respx(tests/agent は偽の pi、tests/eval は偽のルーター)
├─ data/             会話 DB と RAG 索引、コーパス(git 管理外)
└─ logs/             サーバログ・チャットログ(git 管理外)
```

## ロードマップ

| Phase | 内容 | 状態 |
| --- | --- | --- |
| 0 | 土台づくり(git 化、起動スクリプト、ベンチでモデル選定、[journal](docs/journal/phase-0-2.md)) | 完了(ベンチは Phase 6 で計測) |
| 1 | API クライアントと CLI チャット | 完了 |
| 2 | Web チャット UI(FastAPI + フロント) | 完了 |
| 3 | RAG(文書を根拠付きで答える、[journal](docs/journal/phase-3.md)) | 完了(decision accepted) |
| 4 | ツール呼び出しとエージェント(Pi + ハーネス、[journal](docs/journal/phase-4.md)) | 完了(decision accepted) |
| 5 | LoRA ファインチューニング(WSL + unsloth、[training/](training/README.md)、[journal](docs/journal/phase-5.md)) | 完了(decision accepted) |
| 6 | 評価・計測・公開準備([journal](docs/journal/phase-6.md)、[report](docs/harness/reports/phase-6.md)) | 完了(人のレビュー待ち: EVAL-002 / 005 / 006) |

## 学んだこと

- 27B dense の Q4_K_M は約 15 GB あり、12 GB VRAM には収まらない。llama-server は空き VRAM に合わせて GPU 層数を自動調整する(今回は 39/65 層)が、CPU 側に残った層がボトルネックになり生成は約 6 tok/s にとどまる
- llama-server のルーターモード(`--models-preset`)は、`models.ini` の `unsloth/...:UD-Q4_K_M` を `unsloth/...:Q4_K_M` という id で公開する(`UD-` を落とす)。Hugging Face の manifest API では両タグが同じ GGUF に解決されるので実体は同じ。API に渡す id は `/v1/models` で確認する
- llama-server は `--reasoning-format` の既定で Qwen3 の thinking を `reasoning_content` フィールドに分離して返す。ただし本文に `<think>` が混ざる場合もあるので、クライアント側でタグのフォールバック分離も持たせた(タグがチャンク境界で分断されることを考慮する必要がある)
- SSE は「`data:` 行が空行で区切られる」だけの単純な形式で、`[DONE]` が終端。httpx の `aiter_lines()` で十分にパースできる
- FastAPI の `StreamingResponse` に async generator を渡すと、ブラウザが切断したときに generator が閉じられる(`GeneratorExit` / `CancelledError`)。`finally` でそこまでの本文を保存すれば「途中まで」を失わない
- 過去ターンの thinking は llama-server に送らない(Qwen3 の推奨)。DB には残すので UI では見返せる
- llama-server のルーターモードは、Hugging Face のキャッシュに無いモデルには `--hf-repo` を自動で付けない。`models.ini` のセクションに `hf-repo = <repo:tag>` を明示しないと、子プロセスがモデル無しで起動して `loading` のまま戻らない(Phase 3 で判明。Phase 6 の 8B でも再発し、`eval/models-eval.ini` で回避)
- RAG の検索は 1,000 チャンク程度ならベクトル DB なしで足りる。numpy の行列積 1 回(1168 × 1024)で全件のコサイン類似度が出る
- Pi は `max_tokens = min(maxTokens, contextWindow − 推定使用量 − 4096)` で出力長を切り詰める。`contextWindow` を実サーバと同じ 8192 にすると、使用量 4k で max_tokens が 1 になりモデルが何も言えなくなる。`models.json` では 16384 と申告し、ハーネス側のツール出力予算で実コンテキストを 8k 以内に抑えた(Phase 4 で判明)
- Pi の `--thinking off` は llama-server には伝わらない。`before_provider_request` フックでリクエストに `chat_template_kwargs.enable_thinking=false` を足すと Qwen3 の thinking が止まる
- 「モデルは提案し、コードが許可する」は小さく実装できる。Pi の `tool_call` フックで `{block: true}` を返すだけで許可外ツールは実行されず、記録も残る。system prompt の「使わないで」は不要ではないが、それだけに頼らない
- 12 GB の VRAM で 4B の QLoRA は余裕がある(ピーク 5.9 GiB)。難しいのは学習そのものではなく、合成データの品質と「知識が定着したか」を測る方法(Phase 5 → 6)
- LLM 採点は回答モデルと別のモデルで行う。同じモデルが自分を採点すると、同じ誤解を正解扱いする。JSON Schema で出力の形を強制すると採点結果のパース失敗がほぼ無くなる
<!-- learned-phase6:start -->
- RAG と LoRA は代替ではなく補完。同じ 147 問で、RAG は +18〜45 pt、LoRA 単体は +3 pt、LoRA + RAG が決定論の正答率で最も高い(66.7%)。小さいモデルに「語彙」を覚えさせ、「事実」は検索で渡すのが 12 GB では一番効く
- 決定論の採点(キーワード)と LLM 採点は別の軸を見ている。B では「キーワード外れ・LLM 4 以上」が 32 問あった。片方の数字だけで結論を出さない
- 回答の長さは採点に効く。素の 35B は 400 トークンで 33 問が打ち切られ、RAG あり構成は 70〜80 トークンで答える。max_tokens は条件として必ず記録する
- PowerShell 5.1 で `$ErrorActionPreference = "Stop"` のまま `& exe 2>&1` すると、exe が stderr に進行ログを書くだけで NativeCommandError になって止まる(llama-bench で再発)。外部コマンドの呼び出しだけ Continue にする
<!-- learned-phase6:end -->

## 公開(人間が行う手順)

このリポジトリの push とリポジトリの公開設定、Hugging Face へのアップロードは Human Gate(契約 `GATE_EXTERNAL_PUBLISH`)で、
開発セッションは行わない。公開するときは:

1. `git log` でブランチの内容を確認し、`.env`、`data/`、`logs/`、`*.gguf` が追跡されていないことを `git ls-files | Select-String "\.env|^data/|^logs/|\.gguf"` で確かめる(出力が空であること)
2. `uv run pytest` と `.\scripts\agent\verify.ps1 -Agent` が通ることを確認する
3. `git push origin main`、GitHub でリポジトリを public にする
4. LoRA モデルを公開するなら `training/export/README.md`(モデルカード)の数値を確認して Hugging Face にアップロードする
5. `docs/harness/reports/phase-6.decision.json` の human-review 待ち(EVAL-002 / 005 / 006)を確認し、判定を更新する

## ライセンス

[MIT](LICENSE)。モデルは各配布元のライセンス(Qwen3 系は Apache-2.0)に従う。
