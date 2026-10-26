# 内部仕様書(Phase 1〜4)

> **初学者はまず [docs/guide/](guide/00-overview.md) を読む。** この文書はモジュールの責務と処理フローを正確に引くためのリファレンス。
> 「各処理が何のために何をしているか」「どのアルゴリズムをなぜ選んだか」は guide の各章にあり、本書の章との対応表は [README.md](README.md) にある。

実装の構造、モジュールの責務、データの流れを定義する。利用者から見た振る舞いは
[external-spec.md](external-spec.md) を参照。用語(llama-server、SSE、thinking など)は
external-spec の 0 章にある。

## 0. 全体像(最初に読む)

### 0.1 何が動いているか

このリポジトリのコードは 3 つの形で動く。どれも同じ `local_llm` パッケージの中にあり、
llama-server との通信はすべて `LlamaClient` という 1 つのクラスを通る。

```mermaid
flowchart TB
    subgraph proc_cli["プロセス: llm-chat(ターミナル)"]
        cli["cli.py<br>ChatSession"]
        hist["history.py<br>JSONL ログ"]
        c1["client.py<br>LlamaClient"]
    end
    subgraph proc_web["プロセス: llm-web(uvicorn、port 8000)"]
        static["static/<br>index.html, app.js"]
        routes["routes_chat.py<br>/api/*"]
        service["service.py<br>ChatService"]
        db["db.py<br>Database"]
        c2["client.py<br>LlamaClient"]
    end
    subgraph proc_llama["プロセス: llama-server(port 8080、外部ソフト)"]
        llama["OpenAI 互換 API<br>/v1/models, /v1/chat/completions, /health"]
    end
    browser(["ブラウザ"]) -->|GET /| static
    browser -->|fetch + SSE| routes
    routes --> service --> db
    service --> c2
    cli --> hist
    cli --> c1
    c1 -->|HTTP + SSE| llama
    c2 -->|HTTP + SSE| llama
    sqlite[("data/chat.db")] --- db
    jsonl[["logs/chat-*.jsonl"]] --- hist
```

### 0.2 層の分け方

| 層 | ファイル | 責務 | 知っていること / 知らないこと |
| --- | --- | --- | --- |
| 共通層 | `config.py` `types.py` `client.py` | 設定、データ型、llama-server との HTTP 通信 | HTTP と SSE の形式を知っている。CLI も Web も知らない |
| CLI 層 | `cli.py` `history.py` | ターミナルの入出力、履歴の保持、JSONL 記録 | 共通層を使う。Web を知らない |
| Web 層 | `web/app.py` `routes_chat.py` `service.py` `db.py` | HTTP API、会話の永続化、SSE 配信 | 共通層を使う。CLI を知らない |
| フロント | `web/static/*` | 画面描画、ユーザー操作、SSE の受信 | `/api/*` の形だけ知っている。Python を知らない |

依存は上から下への一方向。共通層は単独で `pip install` して使えるライブラリとして成り立つ。

### 0.3 1 リクエストの一生(Web UI の場合)

利用者が「こんにちは」と送ってから、画面に応答が出るまで。番号は下の図と対応する。

```mermaid
flowchart TD
    a1["1. app.js: 入力欄で Enter"] --> a2["2. 会話が無ければ POST /api/conversations で作る"]
    a2 --> a3["3. POST /api/conversations/{id}/messages<br>{content, enable_thinking, model}"]
    a3 --> b1["4. routes_chat: 会話の存在確認 → ChatService へ"]
    b1 --> b2["5. service: user 発言を DB に保存"]
    b2 --> b3["6. service: DB の履歴から messages 配列を組み立てる<br>(過去の thinking は除く)"]
    b3 --> c1["7. LlamaClient: POST /v1/chat/completions stream=true"]
    c1 --> c2["8. llama-server が SSE で delta を返す"]
    c2 --> c3["9. LlamaClient: SSE をパースし ChatChunk(content / reasoning) に変換"]
    c3 --> b4["10. service: ChatChunk をイベント辞書 {type, text} に変換して yield"]
    b4 --> b5["11. routes_chat: data: {json} の SSE 行にしてブラウザへ"]
    b5 --> a4["12. app.js: readSse() で受信。reasoning は折りたたみ、content は Markdown 描画"]
    c2 -->|"[DONE]"| b6["13. service: assistant 発言を DB に保存 → done イベント"]
    b6 --> a5["14. app.js: メタ行に tok/s を表示、会話一覧を更新"]
```

ポイント:

- **保存は 2 回**。user は受信直後(5)、assistant は完了時(13)。途中で失敗しても、そこまでの本文を保存する。
- **SSE は二段構え**。llama-server → `LlamaClient`(OpenAI 形式)、`routes_chat` → ブラウザ(自前の `{type, text}` 形式)。形式を変えているのは、ブラウザに llama-server の生の形を知らせないため。
- **モデルは会話に紐づく**。ドロップダウンで変えると、次の送信時に会話の `model` が更新される。

### 0.4 CLI の場合との違い

| 観点 | Web UI | CLI |
| --- | --- | --- |
| 履歴の置き場 | SQLite(`db.py`) | メモリ上の `list[Message]`(`ChatSession.history`) |
| 記録 | DB がそのまま記録 | `history.py` が JSONL に追記(読み戻しは再開用で、起動時には読まない) |
| 表示 | ブラウザが SSE を受けて描画 | `rich` でターミナルに逐次出力 |
| 失敗時 | 途中までを DB に保存し `error` イベント | user 発言を履歴から外し、赤字表示 |
| 共通部分 | `LlamaClient`、`Message`、`ChatChunk`、設定の読み込み | 同じ |

### 0.5 コードを読む順番

1. `types.py`(50 行): `Message` と `ChatChunk` の形を押さえる。
2. `client.py`: `chat()` と `_parse_stream_event()`。SSE が `ChatChunk` になるまで。
3. `web/service.py`: `stream_reply()`。DB と `LlamaClient` をどうつなぐか。
4. `web/routes_chat.py` → `web/static/app.js` の `sendMessage()` と `readSse()`。
5. `cli.py` の `ask()`。service.py と同じ構造を CLI でやっている。

## 1. 技術スタックと依存

| 層 | 技術 | 用途 |
| --- | --- | --- |
| 言語 | Python 3.12+ | すべてのサーバ側コード |
| パッケージ管理 | uv + hatchling | `pyproject.toml`、`src/` レイアウト |
| HTTP クライアント | httpx(AsyncClient) | llama-server との通信。openai SDK は使わない |
| 設定 | python-dotenv | `.env` の読み込み |
| CLI 表示 | rich | 色付き出力と入力プロンプト |
| Web | FastAPI + uvicorn | REST、SSE、静的配信 |
| DB | sqlite3(標準ライブラリ) | 会話の永続化、RAG の索引(Phase 3) |
| 数値計算 | numpy | 埋め込みベクトルの保存(float32)とコサイン類似度(Phase 3) |
| フロント | 素の HTML/CSS/JS + marked(CDN) | ビルドなし |
| テスト | pytest, pytest-asyncio, respx | llama-server をモック |

## 2. ディレクトリ構成

```
src/local_llm/
├─ __init__.py        公開 API の re-export
├─ config.py          Settings と load_settings()
├─ types.py           Message / Usage / ChatChunk
├─ client.py          LlamaClient, LlamaClientError, ThinkTagSplitter, iter_sse_data
├─ history.py         ChatLogger(JSONL), read_log()
├─ cli.py             ChatSession, parse_args(), main()
├─ rag/               RAG(Phase 3。11 章)
│  ├─ loader.py       Document, load_documents()
│  ├─ chunker.py      Chunk, chunk_text()
│  ├─ embedder.py     Embedder(LlamaClient.embed のバッチ呼び出しと正規化)
│  ├─ store.py        RagStore(documents / chunks 表)
│  ├─ retriever.py    Retriever, RetrievedChunk
│  ├─ prompt.py       RAG_SYSTEM_PROMPT, build_rag_messages()
│  └─ indexer.py      index_corpus(), main()(llm-index)
└─ web/
   ├─ app.py          create_app(), app, main()
   ├─ db.py           Database(SQLite)
   ├─ service.py      ChatService, build_prompt()
   ├─ routes_chat.py  APIRouter(/api/*)
   └─ static/         index.html, app.js, style.css
tests/
├─ conftest.py        共通フィクスチャ(respx のモック、一時 DB)
├─ test_client.py     SSE パース、think 分離、エラー変換
├─ test_cli.py        コマンド処理、ask()、起動時チェック
├─ test_history.py    JSONL ロガー
├─ test_web.py        DB CRUD、API、SSE 配信、部分保存
└─ rag/               Phase 3(conftest で /v1/embeddings を疑似ベクトルでモック)
   ├─ test_chunker_loader.py   見出し分割、サイズと重なり、front matter
   ├─ test_store_retriever.py  embed()、store、indexer の差分スキップ、検索、プロンプト
   ├─ test_rag_api_cli.py      /api/rag/*、use_rag の SSE、CLI の /rag
   └─ eval_retrieval.jsonl     検索精度の評価セット(scripts/agent/eval_retrieval.py が使う)
```

## 3. モジュール間の依存

```mermaid
flowchart TD
    subgraph common["共通層(単独で使える)"]
        client["client.py"]
        types["types.py"]
        config["config.py"]
    end
    subgraph cli_layer["CLI 層"]
        cli["cli.py"]
        history["history.py"]
    end
    subgraph web_layer["Web 層"]
        app["web/app.py"]
        routes["web/routes_chat.py"]
        service["web/service.py"]
        db["web/db.py"]
        static["web/static/*"]
    end

    client --> types
    cli --> client
    cli --> config
    cli --> history
    app --> routes
    app --> db
    app --> config
    app --> client
    routes --> service
    routes --> db
    service --> client
    service --> db
    static -. HTTP / SSE .-> routes
```

原則:

- `client.py` と `types.py` は他の自前モジュールに依存しない(ライブラリとして単独で使える)。
- `web/` から llama-server への HTTP は必ず `LlamaClient` 経由。`routes_chat.py` に httpx は登場しない。
- `service.py` は FastAPI に依存しない(辞書のイテレータを返すだけ)。SSE への文字列化は `routes_chat.py`。
- `db.py` は SQL と行の辞書化だけを持ち、ビジネスルールを持たない(タイトル自動設定だけは例外として `add_message` にある)。

## 4. 共通層

### 4.1 config.py

```python
@dataclass(frozen=True)
class Settings:
    base_url: str = "http://127.0.0.1:8080"
    default_model: str = "unsloth/Qwen3.6-35B-A3B-GGUF:Q4_K_M"
    db_path: Path = Path("data/chat.db")
    timeout: float = 600.0

def load_settings(env_file=None) -> Settings
```

- `load_dotenv(override=False)` のため、既に環境変数があれば `.env` で上書きしない。
- `base_url` は末尾の `/` を落とす。

### 4.2 types.py

| 型 | フィールド | 備考 |
| --- | --- | --- |
| `Message` | `role: str`, `content: str` | `to_dict()` で OpenAI 形式に |
| `Usage` | `prompt_tokens`, `completion_tokens`, `total_tokens` | `from_dict()`。欠けた項目は 0 |
| `ChatChunk` | `content=""`, `reasoning=""`, `finish_reason=None`, `usage=None` | `is_empty` が True なら呼び出し側へ流さない |

すべて frozen dataclass。

### 4.3 client.py

**LlamaClient**

| メソッド | HTTP | 処理 |
| --- | --- | --- |
| `health()` | GET `/health` | 200 かつ `status == "ok"` で True。例外は握りつぶして False |
| `list_models()` | GET `/v1/models` | `data[].id` を返す |
| `chat(...)` | POST `/v1/chat/completions` | 下記 |

`chat()` のリクエスト本文:

```json
{"model": "...", "messages": [...], "stream": true,
 "stream_options": {"include_usage": true},
 "temperature": 0.7,
 "chat_template_kwargs": {"enable_thinking": false}}
```

`stream_options` は `stream=True` のとき、`temperature` は指定時のみ、`chat_template_kwargs` は `enable_thinking=False` のときだけ付く。

ストリーミング時の処理順:

1. `httpx.AsyncClient.stream()` でレスポンスを開く。4xx/5xx なら本文を読んで `LlamaClientError`。
2. `response.aiter_lines()` を `iter_sse_data()` に通し、イベントごとの `data` 文字列を得る。
3. `[DONE]` で終了。それ以外は `_parse_stream_event()` で `ChatChunk` に変換。
   - JSON が壊れていれば読み飛ばす。
   - `{"error": ...}` で `choices` が無ければ `LlamaClientError`。
   - `delta.reasoning_content` → `reasoning`。
   - `delta.content` は `ThinkTagSplitter.feed()` に通し、`<think>` 内なら `reasoning` に回す。
   - `usage` があれば `Usage` に。
4. ストリーム終端で `splitter.flush()` し、残りがあれば最後の `ChatChunk` として返す。
5. `httpx.HTTPError` はすべて `LlamaClientError` に包む。

`stream=False` のときは `_chat_once()` が 1 回の POST で `ChatChunk` を 1 個作る(呼び出し側の形を揃えるため、ジェネレータとして 1 回 yield する)。

`chat()` のストリーミング処理をフローで示す:

```mermaid
flowchart TD
    start([chat 呼び出し]) --> payload["_build_payload()<br>model, messages, stream, stream_options,<br>temperature?, chat_template_kwargs?"]
    payload --> open["httpx stream POST /v1/chat/completions"]
    open --> status{"status >= 400?"}
    status -->|yes| err1["本文を読んで<br>LlamaClientError(status_code)"]
    status -->|no| lines["aiter_lines() → iter_sse_data()<br>1 イベントの data 文字列を得る"]
    lines --> done{"data == [DONE]?"}
    done -->|yes| flush["splitter.flush()<br>保留分があれば最後の ChatChunk"]
    done -->|no| parse["_parse_stream_event(data)"]
    parse --> json{"JSON として読める?"}
    json -->|no| lines
    json -->|yes| iserr{"error があり<br>choices が無い?"}
    iserr -->|yes| err2["LlamaClientError"]
    iserr -->|no| build["delta.reasoning_content → reasoning<br>delta.content → splitter.feed() → content, reasoning<br>usage → Usage"]
    build --> empty{"is_empty?"}
    empty -->|yes| lines
    empty -->|no| yield_["yield ChatChunk"] --> lines
    flush --> fin([終了])
    open -.->|httpx.HTTPError| err3["LlamaClientError(接続できません)"]
```

**ThinkTagSplitter**

状態機械。`_in_think` フラグと保留バッファ `_buf` を持つ。

- `feed(text)`: バッファに足し、`<think>` / `</think>` を探して content と reasoning に振り分ける。
  タグがチャンク境界で切れる場合に備え、末尾がタグの先頭部分と一致する分だけ保留する。
- `flush()`: 保留分を吐き出す。`<think>` が閉じていなければ reasoning 扱い。

```mermaid
stateDiagram-v2
    [*] --> Content
    Content --> Think : "&lt;think&gt;" を見つけた<br>(手前は content として出力)
    Think --> Content : "&lt;/think&gt;" を見つけた<br>(手前は reasoning として出力)
    Content --> Content : タグ無し → 末尾がタグの先頭と一致する分だけ保留、残りを content に
    Think --> Think : タグ無し → 同様に保留、残りを reasoning に
    Content --> [*] : flush() → 保留分を content に
    Think --> [*] : flush() → 保留分を reasoning に(閉じタグ無し)
```

例: `"<thi"` → `"nk>考え"` → `"中</think>答え"` の 3 チャンクが来た場合、
1 回目は全部保留、2 回目でタグが揃って Think に入り `考え` を reasoning に、
3 回目で `中` を reasoning、Content に戻って `答え` を content に出す。

**iter_sse_data**

SSE 仕様のサブセット。`data:` 行を集めて空行で 1 イベント。複数 `data:` は改行連結。`:` コメント行と他フィールドは無視。CRLF 対応。末尾の空行が無くても最後のイベントを出す。

**LlamaClientError**

`status_code: int | None` を持つ。接続失敗は `llama-server に接続できません(...)`、HTTP エラーは `llama-server が HTTP NNN を返しました: <本文先頭 200 文字>`。

## 5. CLI 層(cli.py, history.py)

### 5.1 ChatSession

状態: `client`, `console`, `logger`, `model`, `system_prompt`, `enable_thinking`, `history: list[Message]`。

| メソッド | 役割 |
| --- | --- |
| `build_messages()` | system prompt(あれば)+ history |
| `handle_command(line) -> bool` | スラッシュコマンド。False で終了 |
| `_command_model(arg)` | 引数なしで一覧、ありで切替 |
| `ask(user_text)` | 1 往復。下記 |
| `_print_stats(usage, elapsed, ttft)` | 統計行 |
| `_check_server()` | 起動時の疎通とモデル存在確認 |
| `run()` | 入力ループ |

`run()` のメインループ:

```mermaid
flowchart TD
    start([llm-chat 起動]) --> args["parse_args()<br>既定値は load_settings() から"]
    args --> client["LlamaClient を開く"]
    client --> banner["モデル名と thinking 状態を表示"]
    banner --> check["_check_server()<br>health → 警告 / list_models → モデル存在確認"]
    check --> input["console.input('you> ')"]
    input --> eof{"EOF / Ctrl+C?"}
    eof -->|yes| quit([ログパスを表示して終了])
    eof -->|no| empty{"空行?"}
    empty -->|yes| input
    empty -->|no| slash{"'/' で始まる?"}
    slash -->|yes| cmd["handle_command()"]
    cmd --> cont{"続行?"}
    cont -->|False: /quit| quit
    cont -->|True| input
    slash -->|no| ask["ask(line)"]
    ask --> input
```

`ask()` の流れ:

1. `history` に user を積み、`logger.append("user", ...)`。
2. `client.chat()` を回し、`reasoning` は dim で、`content` は通常色で逐次出力。初トークン時刻を記録。
3. `LlamaClientError` なら赤字表示し `history.pop()` して return(ログには user が残る)。
4. `KeyboardInterrupt` / `CancelledError` は中断表示して続行(途中までを採用)。
5. content か reasoning があれば `history` に assistant を積み、`logger.append("assistant", ..., reasoning=...)`。
6. `_print_stats()`。生成速度 = completion_tokens / (elapsed - ttft)。

### 5.2 ChatLogger

- `path` は初回アクセス時に `log_dir/chat-<YYYYMMDD-HHMMSS>.jsonl` に確定(遅延)。
- `append(role, content, model, **extra)` で 1 行 JSON を追記。`time` はローカルタイムゾーン付き ISO8601。
- `read_log(path)` は読み戻し用。

### 5.3 エントリポイント

`parse_args()` は `load_settings()` の値を既定にする。`main()` は `asyncio.run(_amain())`。Ctrl+C で終了コード 130。

## 6. Web 層

### 6.1 app.py

```python
def create_app(settings=None, *, db=None, client=None) -> FastAPI
app = create_app()   # uvicorn が "local_llm.web.app:app" で参照
def main(argv=None)  # argparse → uvicorn.run()
```

- lifespan で `app.state.settings / db / client` を設定し、`db.init_schema()` を呼ぶ。
- `client` を外から渡さなかった場合だけ終了時に `aclose()` する(テストでは差し替える)。
- `/` は `static/index.html` を `FileResponse` で返す。`/static` は `StaticFiles`。

起動から画面表示までの流れ:

```mermaid
sequenceDiagram
    autonumber
    participant U as 利用者
    participant UV as uvicorn
    participant A as app.py
    participant D as Database
    participant B as ブラウザ (app.js)
    participant R as routes_chat
    participant L as llama-server

    U->>UV: uv run llm-web
    UV->>A: import "local_llm.web.app:app"(create_app() 実行)
    UV->>A: lifespan 開始
    A->>A: load_settings()(.env 読み込み)
    A->>D: init_schema()(data/ 作成、CREATE TABLE IF NOT EXISTS)
    A->>A: LlamaClient 生成を app.state に保持
    UV-->>U: http://127.0.0.1:8000 で待ち受け
    U->>B: ブラウザで開く
    B->>A: GET / → index.html、GET /static/app.js, style.css
    B->>R: GET /api/health
    R->>L: GET /health
    L-->>R: {"status":"ok"}
    R-->>B: {"ok": true}
    B->>R: GET /api/models
    R->>L: GET /v1/models
    L-->>R: {"data":[{"id":...}]}
    R-->>B: {"models":[...], "default":...}
    B->>R: GET /api/conversations
    R-->>B: 一覧
    Note over B: 以後 15 秒ごとに /api/health
```

### 6.2 db.py

スキーマ:

```mermaid
erDiagram
    conversations ||--o{ messages : "ON DELETE CASCADE"
    conversations {
        INTEGER id PK "AUTOINCREMENT"
        TEXT title "NULL 可。最初の user 発言から自動設定"
        TEXT model "NOT NULL"
        TEXT created_at "NOT NULL, UTC ISO8601"
        TEXT updated_at "NOT NULL, 並び順に使う"
    }
    messages {
        INTEGER id PK "AUTOINCREMENT"
        INTEGER conversation_id FK "NOT NULL"
        TEXT role "NOT NULL: user | assistant"
        TEXT content "NOT NULL"
        TEXT reasoning "NULL 可。assistant の thinking"
        TEXT created_at "NOT NULL"
    }
```

インデックス: `idx_messages_conversation ON messages(conversation_id, id)`。

接続方針:

- 呼び出しごとに `sqlite3.connect()` して閉じる(`connect()` コンテキストマネージャ)。FastAPI が同期ルートをスレッドプールで動かしても安全。
- `PRAGMA foreign_keys = ON` を毎回設定(CASCADE に必要)。
- 正常終了で commit、例外で rollback。
- `row_factory = sqlite3.Row` で列名アクセスし、`dict()` にして返す。

時刻は UTC の ISO8601、マイクロ秒付き、`Z` 終端。`updated_at DESC, id DESC` の並びで一覧を返すため、同一秒内でも順序が決まるようにしている。

| メソッド | SQL の要点 |
| --- | --- |
| `init_schema()` | 親ディレクトリ作成 + `executescript(SCHEMA)` |
| `list_conversations()` | 新しい順 |
| `create_conversation(model, title=None)` | INSERT、辞書で返す |
| `get_conversation(id)` | 会話 + messages(id 順)。無ければ None |
| `delete_conversation(id) -> bool` | rowcount > 0 |
| `update_conversation(id, *, model, title)` | 指定列 + updated_at |
| `add_message(id, role, content, reasoning)` | INSERT。role=user かつ title が空なら `make_title()` で設定。updated_at 更新 |
| `list_messages(id)` | id 順 |

`make_title(text)`: 空白と改行を 1 つの空白に潰し、先頭 40 文字。空なら `(無題)`。

会話レコードのライフサイクル:

```mermaid
stateDiagram-v2
    [*] --> 空の会話 : POST /api/conversations<br>(title=NULL, model=既定 or 指定)
    空の会話 --> 会話中 : 最初の user 発言を add_message<br>→ title を自動設定
    会話中 --> 会話中 : add_message(user / assistant)<br>→ updated_at 更新
    会話中 --> 会話中 : model 指定付きの送信<br>→ update_conversation(model)
    空の会話 --> [*] : DELETE
    会話中 --> [*] : DELETE<br>(messages も CASCADE で削除)
```

「+ 新規」ボタンは会話を作らない。最初の送信時に `POST /api/conversations` が呼ばれて初めて行ができる。
そのため、何も送らずに別の会話へ移っても空の行は残らない。

### 6.3 service.py

```python
def build_prompt(history: list[dict]) -> list[Message]
class ChatService:
    def __init__(self, db: Database, client: LlamaClient)
    async def stream_reply(self, conv_id, content, *, enable_thinking=True, model=None) -> AsyncIterator[dict]
```

`build_prompt()` は DB の行から `Message(role, content)` を作る。`reasoning` は含めない。content が空の行は除外する。Phase 3 では検索結果や system メッセージをここで差し込む。

`stream_reply()` の流れ:

1. 会話を取得。無ければ `error` イベントを 1 個出して終了。
2. `model` が指定され会話と異なれば `update_conversation(model=...)`。
3. `add_message(user)` で即保存し、`list_messages()` → `build_prompt()`。
4. `client.chat()` を回し、`reasoning` / `content` をイベントとして yield。断片を `content_parts` / `reasoning_parts` に蓄積。
5. 終了処理は `save_partial()` に集約。`saved` フラグで二重保存を防ぐ。
   - `LlamaClientError`: 保存 → `error` イベント → return。
   - 正常終了または切断(`GeneratorExit` / `CancelledError`): `finally` で保存。
   - 蓄積が空なら保存しない。
6. 正常終了時のみ `done` イベント。`tokens_per_second = completion_tokens / (elapsed - ttft)`、小数 1 桁。

### 6.4 routes_chat.py

`APIRouter(prefix="/api")`。依存の取り出しは `request.app.state` から(`get_db`, `get_client`)。

| ルート | 同期/非同期 | 処理 |
| --- | --- | --- |
| `GET /health` | async | `client.health()` を `{"ok", "llama_base_url"}` に |
| `GET /models` | async | `client.list_models()`。`LlamaClientError` → 502 |
| `GET /conversations` | sync | `db.list_conversations()` |
| `POST /conversations` | sync | `model` 省略時は settings の既定。201 |
| `GET /conversations/{id}` | sync | 404 if None |
| `DELETE /conversations/{id}` | sync | 204 / 404 |
| `POST /conversations/{id}/messages` | async | 404 チェック → `ChatService.stream_reply()` を `StreamingResponse(text/event-stream)` に |

リクエスト型(pydantic): `CreateConversationRequest(model: str | None)`、`SendMessageRequest(content: str [min 1], enable_thinking: bool = True, model: str | None)`。

SSE 文字列化: `data: {json}\n\n`、`ensure_ascii=False`。ヘッダに `Cache-Control: no-cache`、`X-Accel-Buffering: no`。

同期ルートは FastAPI がスレッドプールで実行する。DB が呼び出しごとに接続するのはこのため。

### 6.5 フロント(static/app.js)

IIFE で閉じた 1 ファイル。モジュールバンドラなし。

状態: `{conversations: [], currentId: null, streaming: false, healthy: null}`。

| 関数 | 役割 |
| --- | --- |
| `api(path, options)` | fetch の薄いラッパ。非 2xx は `detail` 付きで throw。204 は null |
| `renderMarkdown(text)` | marked があれば `parse(breaks, gfm)`、無ければエスケープのみ |
| `appendMessage(role, {content, reasoning})` | `<template>` から要素を作り、各パーツへの参照を返す |
| `loadConversations()` / `renderConversationList()` | 一覧の取得と描画 |
| `startNewConversation()` | 画面を空に。会話はまだ作らない |
| `openConversation(id)` | メッセージ復元とモデル反映 |
| `deleteConversation(id)` | confirm → DELETE → 再読込 |
| `loadModels()` | ドロップダウン構築。既定を選択 |
| `checkHealth()` | 15 秒周期。復帰時に `loadModels()` |
| `readSse(response, onEvent)` | `ReadableStream` を `TextDecoder` で読み、`\n\n` 区切りで `data:` を JSON にしてコールバック |
| `sendMessage()` | 会話なければ POST で作成 → user と空の assistant を描画 → POST messages → `readSse` でイベント反映 → 一覧更新 |

`sendMessage()` のイベント反映:

- `reasoning`: `<details>` を表示。本文が無いうちは `open`。
- `content`: `<details>` を閉じ、本文を再描画(累積文字列を毎回 `renderMarkdown`)。
- `done`: メタ行に usage、tok/s、秒、モデル。
- `error`: メタ行に赤字。

送信中は `setStreaming(true)` で入力欄・送信・新規を無効化し、一覧クリックと削除も `state.streaming` で弾く。

`sendMessage()` の処理フロー:

```mermaid
flowchart TD
    enter([Enter / 送信ボタン]) --> check{"空文字 or 送信中?"}
    check -->|yes| stop([何もしない])
    check -->|no| lock["setStreaming(true)<br>入力欄を空にする"]
    lock --> hasconv{"currentId がある?"}
    hasconv -->|no| create["POST /api/conversations<br>{model: ドロップダウンの値}"]
    create --> setid["currentId = 返ってきた id"]
    hasconv -->|yes| draw
    setid --> draw["user の吹き出しと、空の assistant 吹き出しを描画"]
    draw --> post["POST /api/conversations/{id}/messages<br>{content, enable_thinking, model}"]
    post --> ok{"res.ok?"}
    ok -->|no| fail["バナーに '送信に失敗しました'"]
    ok -->|yes| read["readSse(): body を ReadableStream で読み<br>\\n\\n 区切りで data: を JSON に"]
    read --> ev{"type?"}
    ev -->|reasoning| r["thinking を表示、本文が無い間は open"]
    ev -->|content| c["thinking を閉じ、累積本文を Markdown 再描画"]
    ev -->|done| d["メタ行に usage · tok/s · 秒 · model"]
    ev -->|error| e["メタ行に赤字でエラー"]
    r --> read
    c --> read
    d --> endstream
    e --> endstream
    read -->|ストリーム終端| endstream["loadConversations()<br>タイトルを更新"]
    endstream --> unlock["setStreaming(false)<br>入力欄にフォーカス"]
    fail --> unlock
```

`checkHealth()` の 15 秒ポーリング:

```mermaid
flowchart TD
    tick([15 秒ごと / 初期化時]) --> get["GET /api/health"]
    get --> reach{"API に届いた?"}
    reach -->|no| apidown["左下: 'API に接続できません'"]
    reach -->|yes| ok{"ok == true?"}
    ok -->|no| down["左下: '停止中'<br>バナー: 起動手順を案内"]
    ok -->|yes| up["左下: '接続中'<br>バナーを消す"]
    up --> recovered{"前回は停止中だった?<br>or モデル一覧が空?"}
    recovered -->|yes| models["loadModels()<br>ドロップダウンを再構築"]
    recovered -->|no| wait([次の tick まで待つ])
    models --> wait
    down --> wait
    apidown --> wait
```

セキュリティ上の注意: assistant 本文は `innerHTML` に入れる(marked の出力)。ローカル専用のため sanitizer は入れていない。公開する場合は DOMPurify 等が必要。

## 7. データフロー

### 7.1 Web UI で 1 往復

```mermaid
sequenceDiagram
    autonumber
    participant B as ブラウザ (app.js)
    participant R as routes_chat
    participant S as ChatService
    participant D as Database
    participant C as LlamaClient
    participant L as llama-server

    B->>R: POST /api/conversations/{id}/messages
    R->>D: get_conversation(id)
    D-->>R: 会話(無ければ 404)
    R->>S: stream_reply(id, content, enable_thinking, model)
    opt model が会話と異なる
        S->>D: update_conversation(model)
    end
    S->>D: add_message(user)
    S->>D: list_messages(id)
    D-->>S: 履歴
    S->>S: build_prompt(履歴)  ※reasoning は除く
    S->>C: chat(prompt, model, enable_thinking)
    C->>L: POST /v1/chat/completions (stream)
    loop SSE delta ごと
        L-->>C: data: {delta}
        C-->>S: ChatChunk(content / reasoning)
        S-->>R: {"type": "reasoning" | "content", "text"}
        R-->>B: data: {...}
    end
    L-->>C: data: [DONE]
    S->>D: add_message(assistant, content, reasoning)
    S-->>R: {"type": "done", usage, tokens_per_second, ...}
    R-->>B: data: {"type": "done", ...}
```

失敗・切断時:

```mermaid
sequenceDiagram
    participant S as ChatService
    participant D as Database
    participant C as LlamaClient

    alt LlamaClientError
        C-->>S: raise LlamaClientError
        S->>D: add_message(assistant, 途中までの本文)  ※空なら保存しない
        S-->>S: yield {"type": "error", "text"}  (done は出さない)
    else ブラウザ切断
        Note over S: GeneratorExit / CancelledError
        S->>D: finally で save_partial()
    end
```

### 7.2 CLI で 1 往復

```mermaid
sequenceDiagram
    autonumber
    participant U as 利用者
    participant CS as ChatSession
    participant LG as ChatLogger
    participant C as LlamaClient
    participant L as llama-server

    U->>CS: you> 入力
    CS->>CS: history.append(user)
    CS->>LG: append("user", ...)
    CS->>C: chat(build_messages(), model, enable_thinking)
    C->>L: POST /v1/chat/completions (stream)
    loop ChatChunk ごと
        L-->>C: SSE delta
        C-->>CS: ChatChunk
        CS-->>U: reasoning は dim、content は通常色で逐次表示
    end
    alt 正常終了 / Ctrl+C 中断
        CS->>CS: history.append(assistant)
        CS->>LG: append("assistant", ..., reasoning=...)
        CS-->>U: 統計行 (prompt / completion tok, tok/s, 秒)
    else LlamaClientError
        CS->>CS: history.pop()  ※user 発言を外す
        CS-->>U: 赤字でエラー表示
    end
```

## 8. エラー処理の設計

| 発生箇所 | 例外 | 変換先 | 最終的な見え方 |
| --- | --- | --- | --- |
| httpx 接続/タイムアウト | `httpx.HTTPError` | `LlamaClientError` | CLI: 赤字。Web: `error` イベント or 502 |
| HTTP 4xx/5xx | - | `LlamaClientError(status_code=N)` | 同上 |
| SSE 内 `{"error"}` | - | `LlamaClientError` | 同上 |
| 壊れた SSE JSON | `JSONDecodeError` | 読み飛ばし | なし |
| 会話なし | - | 404 / `error` イベント | UI バナー |
| 空 content | pydantic | 422 | UI は送らない |
| クライアント切断 | `GeneratorExit` | `finally` で部分保存 | 次回開くと途中までの本文 |

`health()` だけは例外を投げず False を返す(疎通表示専用のため)。

## 9. テスト戦略

- llama-server は respx で httpx をモックする。GPU や実サーバなしで全テストが通る。
- Web は `TestClient(create_app(settings, db=Database(tmp_path/...), client=LlamaClient(...)))` で起動し、同じ respx モックで llama-server を差し替える。
- SSE のストリーミングは respx に生のバイト列を渡し、`aiter_lines()` の経路を本物のまま通す。
- 検証項目: SSE パース(複数 data、CRLF、`[DONE]`)、`<think>` の分断、ペイロードの形(`stream_options`、`chat_template_kwargs`)、エラー変換、CLI のコマンド状態遷移、CLI の失敗時履歴 pop、DB CRUD と並び順、SSE イベント列と永続化、2 往復目に reasoning を送らないこと、モデル上書き、部分保存、422。
- `asyncio_mode = "auto"` で async テストに marker 不要。

## 10. 拡張ポイント(Phase 3 以降に向けて)

| 拡張 | 変更箇所 |
| --- | --- |
| RAG(検索結果の差し込み) | Phase 3 で実装済み(11 章)。`build_prompt(history, retrieved)` と `sources` イベント |
| ベクトル DB への移行 | `rag/store.py` の `load_embeddings()` / `get_chunks()` と `retriever.py` を差し替える。他は変更不要 |
| ツール呼び出し | `client.chat()` に `tools` 引数と `tool_calls` の delta 解釈を追加 |
| サンプリング設定 | `SendMessageRequest` に項目を足し `client.chat(temperature=...)` へ渡す |
| 認証 / LAN 公開 | `app.py` にミドルウェア。`app.js` の `innerHTML` に sanitizer |

## 11. RAG 層(rag/、Phase 3)

### 11.1 責務の分割

| モジュール | 入力 → 出力 | 外部依存 |
| --- | --- | --- |
| `loader.py` | ディレクトリ → `Document(path, title, text, sha256)` の列。`**/*.md` をパス順に読み、front matter の `title` か最初の `#` をタイトルにする | なし |
| `chunker.py` | 本文 → `Chunk(ordinal, heading, text)` の列。見出しで節に分け、800 文字超は段落境界で切り 100 文字重ねる。コードフェンス内の `#` は無視。40 文字未満の節は次に繰り込む | なし |
| `embedder.py` | テキスト列 → `np.ndarray(float32, L2 正規化)`。`LlamaClient.embed()` を 8 件ずつ呼ぶ | `client.py` |
| `store.py` | `documents` / `chunks` 表の CRUD。埋め込みは `float32.tobytes()` の BLOB | sqlite3, numpy |
| `retriever.py` | 質問 → `RetrievedChunk(n, path, heading, text, score)` の列。全チャンク行列との内積で上位 k | store, embedder |
| `prompt.py` | 履歴 + 検索結果 → system メッセージを先頭に置いた `Message` 列 | types |
| `indexer.py` | コーパス → store の更新(sha256 一致はスキップ、消えた文書は削除)。`llm-index` の入口 | 上記すべて |

llama-server への HTTP は引き続き `LlamaClient` だけが持つ(`embed()` を追加)。`rag/` の中に httpx は出てこない。

### 11.2 DB スキーマ(チャット DB と同じファイル)

```sql
documents(id, path UNIQUE, title, sha256, indexed_at)
chunks(id, document_id → documents.id ON DELETE CASCADE, ordinal, heading, text,
       embedding BLOB, embedding_model, token_count)
```

- `token_count` は文字数(トークナイザに依存しないため)。
- `embedding_model` で絞って読むので、モデルを替えて `--force` で作り直すまで、別モデルのチャンクは検索に混ざらない。
- 文書の更新は `DELETE documents WHERE path` → `INSERT` で丸ごと入れ替える(チャンクは CASCADE で消える)。

### 11.3 検索

1. `store.load_embeddings(model)` で全チャンクの id 列と行列 `(N, D)` を読む(433 × 1024 で約 1.7 MB)。
2. 質問を埋め込み、`scores = matrix @ q`(両方正規化済みなのでコサイン類似度)。
3. `argsort` の上位 k 件(1〜20)を `get_chunks()` で本文つきにし、`n` を 1 から振る。

全件総当たりで十分な理由: N が数千なら行列積はミリ秒。数万を超えたら `store` と `retriever` を sqlite-vec や FAISS に差し替える(10 章)。

### 11.4 データフロー(use_rag=true)

```mermaid
sequenceDiagram
    participant UI as app.js / CLI
    participant R as routes_chat
    participant S as ChatService
    participant RT as Retriever
    participant E as LlamaClient(embed)
    participant L as LlamaClient(chat)
    UI->>R: POST /messages {content, use_rag: true, top_k}
    R->>S: stream_reply(use_rag=True)
    S->>RT: search(content, top_k)
    RT->>E: POST /v1/embeddings
    E-->>RT: ベクトル
    RT-->>S: RetrievedChunk × k
    S-->>UI: data: {"type":"sources", ...}
    S->>L: POST /v1/chat/completions(system に資料)
    L-->>S: SSE
    S-->>UI: content / done(rag: {used, sources})
```

- 検索に使うのは最新のユーザー発言だけ(履歴は使わない)。
- `app.state.retriever` は lifespan で 1 つ作る。`LLAMA_EMBED_BASE_URL` が `LLAMA_BASE_URL` と違うときだけ埋め込み用の `LlamaClient` を別に持ち、終了時に閉じる。
- CLI は `ChatSession(retriever=...)` に同じ `Retriever` を渡し、`ask()` の最初で検索して出典を表示する。

### 11.5 テスト戦略(RAG)

- `/v1/embeddings` は `tests/rag/conftest.py` の `embeddings_side_effect` でモックする。単語ごとに hash で 1 次元を立てる 32 次元の疑似ベクトルなので、同じ語を含む質問とチャンクが近くなり、順位の検証ができる。
- `index_corpus()` は tmp のコーパスで 2 回呼び、2 回目に埋め込み API が呼ばれないこと(差分スキップ)を `route.call_count` で確認する。
- API は `seed_index()` で索引済みの DB を作ってから `TestClient` を起動し、SSE の最初のイベントが `sources` であること、llama-server に送った `messages[0]` が system で資料を含むこと、DB に system が保存されないことを見る。
- 検索精度(RAG-002)は実サーバが要るので pytest ではなく `scripts/agent/eval_retrieval.py` で測り、結果 JSON を `docs/harness/reports/evidence/` に残す。

## 12. エージェント層(agent/、src/local_llm/agent/、Phase 4)

### 12.1 2 つのプロセスと責務

```mermaid
flowchart TB
    subgraph py["Python: uv run llm-agent(src/local_llm/agent/)"]
        runner["runner.py<br>PiRunner / execute()"] --> contract["contract.py<br>load_contract / canonical_hash"]
        runner --> verify["verify.py<br>CHECKS(criterion_id → 検査)"]
        verify --> report["report.py<br>decide_status / write_outputs"]
        verify --> scope_py["scope.py<br>グロブ(TS と同じ規則)"]
    end
    subgraph node["Node: pi --mode rpc(子プロセス)"]
        index["agent/harness/index.ts<br>フックの配線"] --> policy["src/policy.ts<br>HarnessPolicy(純粋)"]
        index --> tools["src/tools.ts<br>rag_search / read_file / list_files / finish"]
        index --> evidence["src/evidence.ts<br>EvidenceLog(JSONL)"]
        policy --> glob["src/glob.ts"]
    end
    runner -- "stdin: {type: prompt}" --> index
    index -- "stdout: RPC イベント" --> runner
    evidence -. "events.jsonl / result.json" .-> verify
```

| 層 | 責務 | 持たないもの |
| --- | --- | --- |
| extension(TS) | Contract をその場で強制する(block / stop)、証拠を書く、ツールを実装する | 判定(accepted かどうか)。Pi のモデル呼び出し自体にも触らない(`before_provider_request` で thinking を切るだけ) |
| runner(Python) | Pi を起動して待つ、Contract の Schema / hash 検証、criterion ごとの検証、decision / report の生成 | ツールの実装、モデルとの対話 |

モデルは extension の `registerTool` で登録されたツールしか見えない(`--no-builtin-tools`)。
見えたとしても `tool_call` フックが `allowed_tools` で block する(`--expose-builtin-tools` はその確認用)。

### 12.2 policy.ts(Pi に依存しない)

| メンバ | 内容 |
| --- | --- |
| `canonicalJson` / `contractHash` | `contract_hash` を除いてキーをソート、区切り無し、非 ASCII をそのまま → sha256。Python の `canonical_hash` と同じ値になる(pytest が node を呼んで照合) |
| `argsHash(tool, input)` | tool_call の引数の正規化 hash。`same_error_limit` の同一性判定にも使う |
| `PATH_ARGS` | ツール名 → (引数名, read か write)。`read_file` / `read` / `grep` / `find` / `ls` は read、`edit` / `write` は write。`list_files` は入れない(結果をフィルタする) |
| `checkPath(input, mode)` | workspace 相対に正規化し、`scope.forbidden` → `scope.read` / `scope.write` の順に判定。forbidden が勝つ。`..` や絶対パスで外に出るものも block |
| `onTurnStart()` | `steps` を増やし `max_steps` を超えたら `budget_exceeded` |
| `decideToolCall(tool, input)` | 判定順: run 停止済み(`run_stopped`)→ finish 済み(`already_finished`)→ 許可外(`tool_not_allowed`)→ `max_tool_calls`(`budget_exceeded`)→ パス(`scope_boundary_exceeded`、`stopReason = scope_violation`) |
| `recordResult(tool, input, isError, errorText)` | 連続する同一エラー(ツール + 引数 + エラー文先頭 200 字の hash)を数え、`same_error_limit` で `same_error_repeated` |
| `mustStop()` / `summary()` | `stopReason` が finished 以外なら true / `run_end` に書く集計 |

グロブ(`glob.ts`、`scope.py` も同じ規則): `**/` は任意の深さ、末尾 `/**` は配下すべて、`*` は `/` 以外、`?` は 1 文字。
パターン `docs/**` は `docs` そのものにも一致する。

### 12.3 index.ts のフック

| Pi のフック | すること |
| --- | --- |
| 起動時 | `HARNESS_CONTRACT` を読み、`contract_hash` を計算して `run_start`(hash が Contract の記載と一致するかも記録) |
| `before_provider_request` | `HARNESS_THINKING=off` のとき `payload.chat_template_kwargs.enable_thinking = false` |
| `turn_start` | `policy.onTurnStart()`。超過なら `stop` を記録し `ctx.abort()` |
| `tool_call` | `policy.decideToolCall()`。block なら `block` を記録し `{block: true, reason, terminate: mustStop()}` を返す(ツールは実行されない)。許可なら `tool_call` を記録 |
| `tool_result` | `tool_result`(is_error、先頭 500 字、文字数)を記録し `policy.recordResult()`。同一エラー反復なら `stop` |
| `agent_before_settle` | `mustStop()` なら `{continue: false}`(Pi に次 turn を続けさせない) |
| `agent_end` | `run_end`(steps / tool_calls / blocks / finished / stop_reason / budget) |

### 12.4 tools.ts

- 4 ツールは `makeTools(policy, log, options)` が返す。`read_file` は policy の `checkPath` を自分でも呼ぶ(フックが外れても守る)。
- 共有の出力予算 `charge(body, tool)`: 残りが 200 字未満なら `output_budget_exhausted` を記録して「finish せよ」のエラー文を返し、足りる分だけ切って返す。
  上限は `maxTotalOutputChars`(6000)、`maxFileChars`(3000)、`ragTextChars`(350)、`ragMaxTopK`(3)。すべて環境変数で変えられる(`HARNESS_*`)。
- `finish` は `result.json` を書き `policy.markFinished()`。2 回目以降は policy が `already_finished` で block する。

### 12.5 runner.py

1. `load_contract()`: JSON を読み、`docs/harness/schemas/task-contract.schema.json` で検証(jsonschema が入っていれば)、`contract_hash` を照合。どちらかが失敗なら `SystemExit`。
2. `PiRunner.build_command()`: `pi --mode rpc --no-session --no-extensions --no-skills --no-prompt-templates --no-context-files --no-themes [--no-builtin-tools] -e agent/harness/index.ts --model ... --thinking ... --system-prompt ...`。
   `build_env()`: `PI_CODING_AGENT_DIR=agent/pi` と `HARNESS_*`。
3. `run()`: 子プロセスを起動し、stdout / stderr をスレッドで読む。stdin に `{"type":"prompt","message":<task>}` を 1 行書いて閉じる。
   stdout の各行を `pi_events.jsonl` に保存しつつ `turn_start`(turn 数)と `message_end`(本文、`errorMessage`)を見る。
   `agent_settled` が来たら終了。`--timeout` 超過は kill して `error` に残す。
   ハーネスの `ctx.abort()` を Pi は `stopReason: "error"` / `"This operation was aborted"` で返すので、runner はこれを `aborted_turns` に数え `error` にはしない。
4. `verify_all()` → `write_outputs()`: 12.6。

### 12.6 verify.py / report.py

- `VerifyContext` は `events.jsonl`、`result.json`、runner の `RunOutcome`、Contract を持つ。`stop_reason` は `run_end` から、`abnormal_stop` は `budget_exceeded` / `same_error_repeated` / `scope_violation` のとき。
- `CHECKS` は `criterion_id → 関数` の辞書(`@check("FINISH_CALLED")` で登録)。Contract の `completion_criteria` を順に見て、無い id は `not_run` + `limitations: ["人のレビューが必要"]`。
- `decide_status()`: `abnormal_stop` → `blocked`、必須に `fail` → `rejected`、必須に `not_run` / 無し → `needs_human`、それ以外 → `accepted`。
- `build_outputs()`: decision(`subject` は `events.jsonl` の sha256、`unresolved_errors` は stop_reason、`reasons`)、verification の配列、Markdown。
  書き出し後に decision / verification を Schema で検証し、失敗したら decision の `unresolved_errors` に `SCHEMA_ERRORS` を足す。

### 12.7 テスト戦略(エージェント)

| 対象 | 方法 |
| --- | --- |
| policy / glob / evidence / tools(TS) | vitest 35 件。Pi を import しない(`index.ts` だけが Pi に依存) |
| hash の一致 | pytest が `node --experimental-strip-types` で `contractHash` を呼び、`canonical_hash` と比較 |
| runner → verify → report | `tests/agent/fake_pi.py`。環境変数 `FAKE_PI_SCENARIO`(finish / budget / bash)で events.jsonl と RPC イベントを再現し、decision が accepted / blocked になることを見る |
| 実機 | `evidence/e2e-*` の 4 run(Completion Report)。モデルの挙動に依存するので pytest には含めない |

## 13. 評価層(eval/、src/local_llm/eval/、Phase 6)

### 13.1 責務の分割

| モジュール | 入力 → 出力 | 外部依存 |
| --- | --- | --- |
| `configs.py` | 構成 id → `EvalConfig(id, label, answer_model, use_rag, top_k)`。A/B の大型モデルは `LLM_DEFAULT_MODEL`、採点モデルの既定は `DEFAULT_GRADER_MODEL` | `config.py` |
| `dataset.py` | `<name>-v<N>.jsonl` → `Dataset(name, version, sha256, items)`。`select_items(n, seed)` は `random.Random(seed).sample` で行番号を選び元の順序で返す | なし |
| `graders.py` | 決定論: `grade_deterministic()`(キーワード再現率・引用)、`retrieval_hit()`。LLM: `build_grader_messages()`、`GRADER_RESPONSE_FORMAT`(json_schema)、`parse_llm_grade()` | `types.py` |
| `runner.py` | `EvalRunner.run()`: load 回答モデル → 全問回答 → unload → load 採点モデル → 全問採点 → unload。`GraderSeparationError` を生成時に投げる | `client.py`, `rag/` |
| `report.py` | `compute_metrics()`、`write_result()`(JSON + Markdown)、`render_comparison()`、`latest_per_config()` | なし |
| `cli.py` | `llm-eval` の argparse と `asyncio.run` | 上記すべて |

llama-server への HTTP は `LlamaClient` だけが持つ。Phase 6 で足したのは `chat(max_tokens=, response_format=)` と
ルーターモードの `router_models()` / `router_load()` / `router_unload()`(`GET /models`、`POST /models/load|unload`)。

### 13.2 データフロー(構成 B、RAG あり)

```mermaid
sequenceDiagram
    participant cli as llm-eval
    participant r as EvalRunner
    participant srv as llama-server(ルーター)
    cli->>r: run(dataset)
    r->>srv: POST /models/load {answer_model} → GET /models が loaded になるまで待つ
    loop 各問
        r->>srv: POST /v1/embeddings(質問)→ Retriever が上位 k 件
        r->>srv: POST /v1/chat/completions(system: RAG 指示 + 資料, user: 質問, temperature 0, thinking off, max_tokens 400)
        r->>r: grade_deterministic / retrieval_hit → *.partial.json
    end
    r->>srv: POST /models/unload {answer_model}
    r->>srv: POST /models/load {grader_model}
    loop 各問
        r->>srv: POST /v1/chat/completions(採点プロンプト, response_format=json_schema)
        r->>r: parse_llm_grade
    end
    r->>srv: POST /models/unload {grader_model}
    r-->>cli: result dict → write_result(JSON, Markdown)
```

12 GB の VRAM に 35B(GPU 側約 3 GB + CPU 側 MoE)と 8B(約 5.5 GB)を同時に載せないため、回答と採点を分けて順に実行する。
埋め込みモデル(約 0.7 GB)は常駐させたままにする。

### 13.3 結果 JSON と採点モデル分離の検証(EVAL-003)

- `eval/schemas/qa-result.schema.json`(draft 2020-12)。`grader.differs_from_answer_model` は `const: true`、`metrics` と `items[]` は `additionalProperties: false`。
- JSON Schema は 2 つのプロパティの値の比較を表現できないので、`scripts/agent/check_eval_results.py` が `grader.model != answer.model` を文字列で比較する。
  `verify.ps1` の「eval results schema」ステップで毎回走る。結果ファイルが無い環境では「対象なし」で通す。
- 多重の守り: runner は生成時に `GraderSeparationError`、CLI は終了コード 2、Schema は `const true`、チェッカは文字列比較。

### 13.4 テスト戦略(評価)

| 対象 | 方法 |
| --- | --- |
| 採点規則・データセット・構成 | `tests/eval/test_graders_dataset.py`(HTTP なし) |
| CLI 一式 | `tests/eval/test_runner_cli.py`。`tests/eval/conftest.py` の `FakeRouter` が `/models`、`/models/load`、`/models/unload`、`/v1/chat/completions` を respx で受け、採点モデルには JSON の点数を返す。載せ替え順、`temperature 0` / `chat_template_kwargs` / `response_format` の送信、Schema 適合、同一モデル拒否(exit 2)を見る |
| 実機 | `eval/results/*.json`(Completion Report から参照)。モデルの出力に依存するので pytest には含めない |
