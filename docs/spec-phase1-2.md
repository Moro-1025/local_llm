# Phase 1 / Phase 2 仕様

README のロードマップのうち、Phase 1(API クライアントと CLI チャット)と
Phase 2(Web チャット UI)の実装仕様。決定済みの方針と、実装時の判断基準をまとめる。

## 決定事項

| 論点 | 決定 | 理由 |
| --- | --- | --- |
| 言語 / パッケージ管理 | Python 3.12 + uv、`pyproject.toml` で `src/local_llm/` をパッケージ化 | 既に uv がある。Phase 3 以降も同じ土台で積む |
| HTTP クライアント | httpx で OpenAI 互換 API を直接叩く(openai SDK は使わない) | SSE のパースまで自分で書くことが学習目的に合う。依存が軽い |
| フロントエンド | 素の HTML + CSS + JS(ビルドなし、FastAPI の static で配信) | 範囲を小さくして Phase 3 以降に時間を回す |
| 会話の永続化 | SQLite(標準ライブラリ `sqlite3`、ファイルは `data/chat.db`) | 会話一覧・再開・削除ができる。Phase 3 でも同じ DB を使う |
| 認証 | なし(127.0.0.1 前提) | 個人利用。`-Expose` 相当の公開は Phase 2 の範囲外 |
| ポート | FastAPI 8000、llama-server 8080 | README と揃える |
| テスト | pytest + respx で llama-server をモック。GPU なしで通る | CI や別 PC でも検証できる |

## 共通: 設定

- 設定は環境変数と `.env`(python-dotenv)で読む。`.env.example` をコミットする
  - `LLAMA_BASE_URL`(既定 `http://127.0.0.1:8080`)
  - `LLM_DEFAULT_MODEL`(既定 `unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_M`。ダウンロード済みで速い)
  - `CHAT_DB_PATH`(既定 `data/chat.db`)
- `data/` と `.env` は `.gitignore` に追加する

## Phase 1: API クライアントと CLI チャット

### パッケージ構成

```
src/local_llm/
├─ __init__.py
├─ config.py        設定の読み込み(上記の環境変数)
├─ client.py        LlamaClient: llama-server の OpenAI 互換 API ラッパ
├─ types.py         Message / ChatChunk などの dataclass
├─ history.py       JSONL への会話ログ保存(CLI 用)
└─ cli.py           CLI チャット(エントリポイント `llm-chat`)
```

### client.py の要件

- `LlamaClient(base_url, timeout)` を httpx.AsyncClient で実装する
- `list_models() -> list[str]`: `GET /v1/models` から id を取り出す
- `chat(messages, model, *, stream=True, temperature=None, enable_thinking=True) -> AsyncIterator[ChatChunk]`
  - `POST /v1/chat/completions` を `stream: true` で呼び、SSE(`data: ...` 行、`[DONE]` 終端)を自前でパースする
  - `ChatChunk` は `content: str`、`reasoning: str`(Qwen3 の thinking 部分)、`finish_reason: str | None`、`usage`(最終チャンクのみ)を持つ
  - thinking は llama.cpp の `--reasoning-format` 既定で `reasoning_content` フィールドに分離されて返るので、それを `reasoning` に入れる。本文に `<think>` が混ざる場合に備えて `<think>...</think>` のフォールバック分離も行う
  - `enable_thinking=False` のときはリクエストに `chat_template_kwargs: {"enable_thinking": false}` を付ける
- `health() -> bool`: `GET /health` が `ok` なら True
- エラー: 接続失敗・HTTP 4xx/5xx は `LlamaClientError` にまとめる(メッセージにステータスと本文の先頭を含める)
- 同期版は作らない。CLI は `asyncio.run` で呼ぶ

### cli.py の要件

- `llm-chat [--model M] [--system S] [--no-think] [--base-url U]` で起動
- 入力ループ。応答はストリーミングで逐次表示する。thinking は `--no-think` でなければ薄い色で表示し、本文と区別する
- スラッシュコマンド
  - `/model` で一覧表示、`/model <id>` で切替
  - `/clear` で履歴を消す
  - `/system <text>` で system prompt を差し替える
  - `/think on|off` で thinking を切替
  - `/quit` または Ctrl+D で終了
- 会話は `logs/chat-<日時>.jsonl` に 1 メッセージ 1 行で保存する(role, content, model, 時刻)
- 応答終了時にトークン数と生成速度(tok/s)を 1 行で表示する(usage とタイマーから算出)
- 表示には rich を使ってよい(依存はこれと httpx、python-dotenv 程度に留める)

### Phase 1 の完了条件

- サーバを起動した状態で `uv run llm-chat` が会話でき、モデル切替と thinking 切替が動く
- `uv run pytest` が通る。SSE パース、thinking 分離、エラー変換をモックで検証する
- README の「使い方」に CLI の起動方法を追記し、ロードマップの Phase 1 を「完了」にする

## Phase 2: Web チャット UI

### 構成

```
src/local_llm/web/
├─ app.py           FastAPI アプリ(エントリポイント `llm-web` = uvicorn 起動)
├─ db.py            SQLite のスキーマとクエリ
├─ routes_chat.py   /api/* のエンドポイント
└─ static/
    ├─ index.html
    ├─ app.js
    └─ style.css
```

### DB スキーマ(SQLite)

```sql
conversations(id INTEGER PK, title TEXT, model TEXT, created_at TEXT, updated_at TEXT)
messages(id INTEGER PK, conversation_id INTEGER FK, role TEXT, content TEXT,
         reasoning TEXT, created_at TEXT)
```

- title は最初のユーザー発言の先頭 40 文字を自動で入れる
- 起動時に `CREATE TABLE IF NOT EXISTS` で作る。マイグレーション機構は作らない

### API

| メソッド | パス | 内容 |
| --- | --- | --- |
| GET | `/api/models` | llama-server の `/v1/models` を中継 |
| GET | `/api/health` | llama-server の疎通を返す |
| GET | `/api/conversations` | 一覧(新しい順) |
| POST | `/api/conversations` | 新規作成 `{model}` |
| GET | `/api/conversations/{id}` | メッセージ込みで返す |
| DELETE | `/api/conversations/{id}` | 削除 |
| POST | `/api/conversations/{id}/messages` | `{content, enable_thinking}` を受け、SSE で応答を流す |

- SSE のイベント形式は `data: {"type":"reasoning"|"content"|"done"|"error", "text":...}`。`done` に usage と tok/s を含める
- ユーザー発言は受信時に即 DB へ保存し、アシスタント発言は生成完了時に保存する。途中で切断されたら、そこまでの本文を保存する
- 会話の過去メッセージは DB から組み立てて llama-server に送る(reasoning は送らない)
- llama-server に届かないときは `/api/health` が false を返し、UI にその旨を出す
- Phase 1 の `LlamaClient` をそのまま使う。FastAPI 側で HTTP を直接書かない

### フロント

- 左にサイドバー(会話一覧、新規作成、削除)、右にチャット欄
- 上部にモデル選択ドロップダウン(`/api/models`)と thinking のトグル
- 応答は `fetch` + `ReadableStream` で SSE を読み、逐次描画する。thinking は折りたたみ表示
- Markdown 表示は marked などの CDN ライブラリ 1 つまで可。コードブロックはそのまま `<pre>` でよい
- 送信は Enter、改行は Shift+Enter
- 外部 CSS フレームワークは使わない。ダークテーマ 1 種類でよい

### Phase 2 の完了条件

- `uv run llm-web` で http://127.0.0.1:8000 を開き、会話の作成・継続・削除とモデル切替ができる
- ブラウザをリロードしても会話が残る
- `uv run pytest` が通る。API は FastAPI の TestClient、llama-server は respx でモックする
- README に起動方法とスクリーンショット用の説明を追記し、ロードマップの Phase 2 を「完了」にする

## 実装の進め方

- Phase 1 → Phase 2 の順に、それぞれ別コミットにする
- 各 Phase の完了条件を満たしてから次へ進む
- 迷ったら「Phase 3(RAG)で FastAPI の `/messages` に検索を挟める構造か」を基準に選ぶ
