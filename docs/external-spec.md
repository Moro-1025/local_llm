# 外部仕様書(Phase 1 / Phase 2)

利用者から見た振る舞いを定義する。内部構造は [internal-spec.md](internal-spec.md)、
開発時の決定経緯は [spec-phase1-2.md](spec-phase1-2.md) を参照。

対象バージョン: local-llm 0.1.0(Phase 2 完了時点)

## 1. システム概要

llama.cpp の `llama-server`(OpenAI 互換 API)で動くローカル LLM に対して、
ターミナルとブラウザの 2 通りで会話できる。

```
利用者 ─┬─ CLI チャット (llm-chat) ───────┐
        │                                   ├──→ llama-server (127.0.0.1:8080) ──→ GPU/CPU
        └─ ブラウザ ──→ Web UI (llm-web) ──┘
                        127.0.0.1:8000
                        └── SQLite (data/chat.db)
```

| 構成要素 | 役割 |
| --- | --- |
| llama-server | モデルの読み込みと推論。`scripts/start-server.ps1` で起動する(本仕様の範囲外) |
| `llm-chat` | ターミナルで会話する CLI |
| `llm-web` | ブラウザ用チャット UI と REST/SSE API を提供する Web サーバ |
| `local_llm` パッケージ | 自分の Python コードから llama-server を呼ぶためのクライアント |

## 2. 前提と動作環境

| 項目 | 内容 |
| --- | --- |
| OS | Windows 11(PowerShell 5.1)。Python 部分は OS 非依存 |
| Python | 3.12 以上、パッケージ管理は uv |
| llama-server | ルーターモードで起動し、`/v1/models` `/v1/chat/completions` `/health` を提供すること |
| ネットワーク | すべて 127.0.0.1 で完結する。認証はない |
| ブラウザ | `fetch` の `ReadableStream`、`<details>`、`<template>` に対応したモダンブラウザ |

利用者は `uv sync` で依存を入れ、llama-server を起動してから各コマンドを使う。

## 3. 設定

環境変数、またはカレントディレクトリの `.env` で設定する。
優先順位は「設定済みの環境変数 > `.env` > 既定値」。

| 変数 | 既定値 | 意味 |
| --- | --- | --- |
| `LLAMA_BASE_URL` | `http://127.0.0.1:8080` | llama-server の URL。末尾の `/` は無視する |
| `LLM_DEFAULT_MODEL` | `unsloth/Qwen3.6-35B-A3B-GGUF:Q4_K_M` | 既定モデル id。`/v1/models` が返す id を指定する |
| `CHAT_DB_PATH` | `data/chat.db` | Web UI の会話を保存する SQLite ファイル |
| `LLAMA_TIMEOUT` | `600` | llama-server への HTTP タイムアウト(秒) |

モデル id について: `models.ini` のプリセット名に含まれる `UD-` は llama-server が公開時に
落とすため、API に渡す id は `UD-` なしの形になる。

## 4. CLI チャット `llm-chat`

### 4.1 起動

```
uv run llm-chat [--model ID] [--system TEXT] [--no-think] [--base-url URL] [--timeout SEC] [--log-dir DIR]
```

| オプション | 既定値 | 意味 |
| --- | --- | --- |
| `--model`, `-m` | `LLM_DEFAULT_MODEL` | 使うモデル id |
| `--system`, `-s` | なし | system prompt |
| `--no-think` | off | Qwen3 の thinking(推論過程)を無効にする |
| `--base-url` | `LLAMA_BASE_URL` | llama-server の URL |
| `--timeout` | `LLAMA_TIMEOUT` | HTTP タイムアウト(秒) |
| `--log-dir` | `logs` | 会話ログの保存先ディレクトリ |

### 4.2 起動時の振る舞い

1. 1 行目に現在のモデル名と thinking の状態を表示する。
2. llama-server の疎通を確認し、届かなければ赤字で警告して起動手順を案内する(終了はしない)。
3. 指定モデルがサーバのモデル一覧に無ければ黄字で警告し、利用できる id を列挙する。

### 4.3 対話

- プロンプト `you>` に入力する。空行は無視する。
- `/` で始まる行はコマンド、それ以外はユーザー発言として送信する。
- 応答はストリーミングで逐次表示する。
  - thinking があれば先に `[thinking]` という見出しのあと薄い色で表示する。
  - 本文は `assistant>` のあとに通常色で表示する。
- 応答の末尾に統計を 1 行で表示する。
  形式: `(prompt N tok / completion M tok, X.X tok/s, Y.Ys)`。
  生成速度は初トークンまでの待ち時間を除いた時間で割る。usage が取れなければ `(Y.Ys, usage なし)`。
- Ctrl+C で応答を中断できる。途中までの本文は履歴に残る。
- llama-server がエラーを返した場合は赤字で表示し、その発言は履歴から外す(再送しても二重にならない)。

### 4.4 コマンド

| コマンド | 動作 |
| --- | --- |
| `/model` | サーバのモデル一覧を表示する。現在のモデルに `*` を付ける |
| `/model <id>` | モデルを切り替える(存在確認はしない) |
| `/clear` | 会話履歴を消す。system prompt とモデルは保持する |
| `/system <text>` | system prompt を差し替える。引数なしで外す |
| `/think on\|off` | thinking の有無を切り替える |
| `/help` | コマンド一覧を表示する |
| `/quit`, `/exit` | 終了する。Ctrl+D / Ctrl+Z、Ctrl+C でも終了する |

不明なコマンドは赤字で知らせ、`/help` を案内する。

### 4.5 会話ログ

- `logs/chat-<YYYYMMDD-HHMMSS>.jsonl` に 1 メッセージ 1 行で追記する。
- ファイルは最初のメッセージを書くときに作る(何も話さず終了すれば作らない)。
- 1 行の形式: `{"time": ISO8601, "role": "user"|"assistant", "content": str, "model": str, "reasoning": str|null}`。
  `reasoning` は assistant 行のみ。
- 終了時にログファイルのパスを表示する。

## 5. Web チャット UI `llm-web`

### 5.1 起動

```
uv run llm-web [--host HOST] [--port PORT] [--reload]
```

| オプション | 既定値 | 意味 |
| --- | --- | --- |
| `--host` | `127.0.0.1` | 待ち受けアドレス |
| `--port` | `8000` | 待ち受けポート |
| `--reload` | off | コード変更で自動再起動(開発用) |

起動時に `CHAT_DB_PATH` のテーブルを作る(存在すれば何もしない)。親ディレクトリも作る。

### 5.2 画面構成

```
┌──────────────┬────────────────────────────────────────────┐
│ local_llm    │ モデル [▼ select]  [x] thinking   会話タイトル │
│ [+ 新規]     ├────────────────────────────────────────────┤
│──────────────│ (バナー: llama-server 停止中など)            │
│ 会話 A    ×  │                                            │
│ 会話 B    ×  │  あなた: ...                                │
│ 会話 C    ×  │  assistant: ▸ thinking (折りたたみ)         │
│              │             本文 (Markdown)                 │
│              │             prompt 12 / completion 80 tok · 25.3 tok/s · 3.2s · model │
│──────────────├────────────────────────────────────────────┤
│ llama-server:│ [メッセージを入力…               ] [送信]   │
│ 接続中       │                                            │
└──────────────┴────────────────────────────────────────────┘
```

| 領域 | 内容 |
| --- | --- |
| サイドバー上部 | ブランド名と「+ 新規」ボタン |
| 会話一覧 | 更新日時の新しい順。クリックで開く。`×` で削除(確認ダイアログあり)。現在の会話を強調表示。ホバーでモデルと更新日時を表示 |
| サイドバー下部 | llama-server の疎通状態(「確認中…」「接続中」「停止中」「API に接続できません」) |
| ツールバー | モデル選択ドロップダウン、thinking チェックボックス、会話タイトル |
| バナー | エラーや停止中の案内。問題が解消されると消える |
| メッセージ欄 | 会話がないときは案内文を表示する |
| 入力欄 | 自動で高さが伸びる(最大 200px)。送信ボタン |

### 5.3 操作

| 操作 | 振る舞い |
| --- | --- |
| 送信(Enter またはボタン) | 空文字は送らない。Shift+Enter は改行。日本語入力の変換確定 Enter では送信しない |
| 会話がない状態で送信 | ドロップダウンのモデルで会話を自動作成してから送る |
| 送信中 | 入力欄・送信・新規ボタンを無効化する。会話の切替と削除もできない |
| 応答の表示 | thinking は折りたたみで表示し、本文が始まるまでは開いておき、始まったら閉じる。本文は逐次 Markdown 描画する |
| 応答完了 | メッセージ下に `prompt N / completion M tok · X tok/s · Y s · モデル id` を表示する。会話一覧とタイトルを更新する |
| エラー | メッセージ下に赤字で `エラー: ...` を出す。送信自体が失敗した場合はバナーに出す |
| モデル変更 | ドロップダウンの値は次の送信時に会話のモデルとして保存される |
| thinking 切替 | チェックの状態が次の送信に適用される |
| 会話を開く | メッセージを復元し、会話に保存されたモデルをドロップダウンに反映する |
| 「+ 新規」 | 画面を空にする。会話は最初の送信時に作られる |
| 削除 | 確認後に削除。開いていた会話なら画面を空にする |

### 5.4 自動処理

- 15 秒ごとに llama-server の疎通を確認し、サイドバー下部とバナーを更新する。
- 停止中から復帰したときはモデル一覧を取り直す。
- 会話タイトルは最初のユーザー発言の先頭 40 文字(改行は空白に潰す)を自動設定する。
- 会話は SQLite に保存され、リロードやサーバ再起動後も残る。

### 5.5 Markdown 表示

- assistant の本文は marked(CDN、GFM、改行を `<br>` に変換)で描画する。
- CDN が読めない場合はプレーンテキストで表示する。
- ユーザー発言は Markdown 解釈せずそのまま表示する。

## 6. HTTP API(Web UI のバックエンド)

ベース URL は `http://127.0.0.1:8000`。すべて `/api` 配下、JSON、認証なし。
自動ドキュメントは `/docs`(Swagger UI)。

### 6.1 一覧

| メソッド | パス | 成功 | 説明 |
| --- | --- | --- | --- |
| GET | `/api/health` | 200 | llama-server の疎通 |
| GET | `/api/models` | 200 | モデル一覧(llama-server を中継) |
| GET | `/api/conversations` | 200 | 会話一覧(新しい順) |
| POST | `/api/conversations` | 201 | 会話を作る |
| GET | `/api/conversations/{id}` | 200 | 会話をメッセージ込みで取得 |
| DELETE | `/api/conversations/{id}` | 204 | 会話を削除 |
| POST | `/api/conversations/{id}/messages` | 200 (SSE) | 発言を送り応答をストリーミング |
| GET | `/` | 200 | UI の HTML |
| GET | `/static/*` | 200 | UI の静的ファイル |

### 6.2 各エンドポイント

**GET /api/health**

```json
{"ok": true, "llama_base_url": "http://127.0.0.1:8080"}
```

llama-server に届かなくても 200 を返し、`ok` が false になる。

**GET /api/models**

```json
{"models": ["unsloth/Qwen3.6-35B-A3B-GGUF:Q4_K_M", "..."], "default": "unsloth/Qwen3.6-35B-A3B-GGUF:Q4_K_M"}
```

llama-server に届かない、またはエラーのときは 502 と `{"detail": "..."}`。

**GET /api/conversations**

```json
{"conversations": [{"id": 3, "title": "こんにちは", "model": "...", "created_at": "...", "updated_at": "..."}]}
```

**POST /api/conversations**

リクエスト `{"model": "..."}`。`model` は省略可(null)で、省略時は `LLM_DEFAULT_MODEL`。
レスポンスは作成した会話(201)。`title` は null で始まる。

**GET /api/conversations/{id}**

```json
{"id": 3, "title": "...", "model": "...", "created_at": "...", "updated_at": "...",
 "messages": [{"id": 1, "role": "user", "content": "...", "reasoning": null, "created_at": "..."}]}
```

存在しなければ 404。

**DELETE /api/conversations/{id}**

204。存在しなければ 404。メッセージも一緒に消える。

**POST /api/conversations/{id}/messages**

リクエスト:

```json
{"content": "質問文", "enable_thinking": true, "model": "任意: 会話のモデルを変更"}
```

- `content` は 1 文字以上必須(空は 422)。
- `model` を指定し、会話のモデルと異なれば会話のモデルを更新してから送る。
- 会話が存在しなければ 404。

レスポンスは `text/event-stream`。各イベントは `data: <JSON>\n\n`:

| type | フィールド | 意味 |
| --- | --- | --- |
| `reasoning` | `text` | thinking の断片 |
| `content` | `text` | 本文の断片 |
| `done` | `model`, `usage`, `tokens_per_second`, `elapsed_seconds` | 完了。`usage` は `{prompt_tokens, completion_tokens, total_tokens}` または null |
| `error` | `text` | 失敗。これが最後のイベントになり `done` は来ない |

保存の規則:

- ユーザー発言は受信直後に保存する(llama-server が落ちていても残る)。
- assistant の発言は完了時に保存する。エラーや切断のときは、そこまでの本文と thinking を保存する。何も生成されていなければ保存しない。
- 過去の thinking は次の送信時に llama-server へ送らない。

## 7. Python API(`local_llm` パッケージ)

```python
from local_llm import LlamaClient, LlamaClientError, Message, ChatChunk, Usage
```

| 名前 | 内容 |
| --- | --- |
| `LlamaClient(base_url, timeout)` | 非同期クライアント。`async with` で使う |
| `await client.health() -> bool` | 疎通。失敗時は例外でなく False |
| `await client.list_models() -> list[str]` | モデル id 一覧 |
| `client.chat(messages, model, *, stream=True, temperature=None, enable_thinking=True)` | `ChatChunk` の非同期イテレータ。`stream=False` でも 1 個のチャンクを返す |
| `Message(role, content)` | 送信メッセージ |
| `ChatChunk(content, reasoning, finish_reason, usage)` | 応答断片。`usage` は最終チャンクのみ |
| `LlamaClientError` | 接続失敗、タイムアウト、HTTP 4xx/5xx、ストリーム内エラーをまとめた例外。`status_code` 属性を持つ |

## 8. エラー時の振る舞いまとめ

| 状況 | CLI | Web UI |
| --- | --- | --- |
| llama-server 停止 | 起動時に警告。送信時はエラー表示し発言を履歴から外す | 下部が「停止中」、バナー表示。送信するとユーザー発言は保存され `error` イベントが届く |
| モデル id が存在しない | 起動時に警告して一覧表示 | llama-server のエラーが `error` イベントで届く |
| 応答中の中断 | Ctrl+C。途中までを履歴に残す | ブラウザを閉じる等。途中までを DB に保存 |
| 空入力 | 無視 | 送信しない(API 直叩きは 422) |

## 9. 範囲外(Phase 3 以降)

- 文書検索と根拠付き回答(RAG)
- ツール呼び出し、エージェント
- 認証、LAN 公開、複数ユーザー
- 会話のエクスポート、検索、タイトルの手動変更
- temperature などのサンプリング設定 UI
