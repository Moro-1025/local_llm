# local_llm

llama.cpp を使って自宅 PC(RTX 5070 / 12 GB)でローカル LLM を動かし、
API クライアント → チャット UI → RAG → エージェント → ファインチューニングと
段階的に積み上げていく学習・ポートフォリオ用プロジェクト。

> 現在のフェーズ: **Phase 1 完了**(API クライアントと CLI チャット)。Phase 0 のベンチ計測は別途進める

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
| `LLM_DEFAULT_MODEL` | `unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_M` | 既定モデル |
| `CHAT_DB_PATH` | `data/chat.db` | Web UI の会話 DB(Phase 2) |
| `LLAMA_TIMEOUT` | `600` | HTTP タイムアウト(秒) |

### CLI チャット(Phase 1)

llama-server を起動した状態で:

```powershell
uv run llm-chat                       # 既定モデルで開始
uv run llm-chat --model unsloth/Qwen3.8-27B-GGUF:UD-Q4_K_M
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
| `/quit` | 終了(Ctrl+D / Ctrl+Z でも終了) |

### テスト

llama-server は respx でモックするので GPU なしで通る:

```powershell
uv run pytest
```

### API クライアントを自分のコードから使う

```python
import asyncio
from local_llm import LlamaClient, Message

async def main():
    async with LlamaClient("http://127.0.0.1:8080") as client:
        print(await client.list_models())
        async for chunk in client.chat([Message("user", "こんにちは")], "unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_M"):
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
│   ├─ bench.ps1          llama-bench で速度計測
│   └─ ctx-vram.ps1       ctx-size ごとの VRAM 使用量計測
├─ bench/            計測結果(Markdown)
├─ docs/             仕様書(spec-phase1-2.md など)
├─ src/local_llm/    Python パッケージ
│   ├─ config.py         設定(環境変数 / .env)
│   ├─ types.py          Message / ChatChunk / Usage
│   ├─ client.py         LlamaClient(httpx、SSE 自前パース、thinking 分離)
│   ├─ history.py        CLI の JSONL ログ
│   └─ cli.py            CLI チャット(llm-chat)
├─ tests/            pytest + respx
└─ logs/             サーバログ・チャットログ(git 管理外)
```

## ロードマップ

| Phase | 内容 | 状態 |
| --- | --- | --- |
| 0 | 土台づくり(git 化、起動スクリプト、ベンチでモデル選定) | 進行中(ベンチ計測待ち) |
| 1 | API クライアントと CLI チャット | 完了 |
| 2 | Web チャット UI(FastAPI + フロント) | |
| 3 | RAG(文書を根拠付きで答える) | |
| 4 | ツール呼び出しとエージェント | |
| 5 | LoRA ファインチューニング | |
| 6 | 評価・計測・公開 | |

## 学んだこと

- 27B dense の Q4_K_M は約 15 GB あり、12 GB VRAM には収まらない。llama-server は空き VRAM に合わせて GPU 層数を自動調整する(今回は 39/65 層)が、CPU 側に残った層がボトルネックになり生成は約 6 tok/s にとどまる
- llama-server は `--reasoning-format` の既定で Qwen3 の thinking を `reasoning_content` フィールドに分離して返す。ただし本文に `<think>` が混ざる場合もあるので、クライアント側でタグのフォールバック分離も持たせた(タグがチャンク境界で分断されることを考慮する必要がある)
- SSE は「`data:` 行が空行で区切られる」だけの単純な形式で、`[DONE]` が終端。httpx の `aiter_lines()` で十分にパースできる
- (ベンチ後に追記)
