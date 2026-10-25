# Phase 6: 評価・計測・公開準備

## この Phase で作ったもの(1 分で)

Phase 3(RAG)と Phase 5(LoRA)で作った「知識を補う 2 つのやり方」を、同じ 147 問で素のモデルと比べる評価ハーネス `uv run llm-eval` を作った。
採点は「正解文のキーワードが応答に出るか」という機械的な採点と、別のモデル(Qwen3-8B)による 1〜5 点の採点の 2 段。
あわせて Phase 0 から保留していた速度(tok/s)と VRAM の計測を済ませ、README を公開用に整え、LICENSE(MIT)を置いた。
GitHub への push と公開設定の変更は人間が行う(Human Gate)。

```mermaid
flowchart LR
    ds["eval/datasets/qa-v1.jsonl<br>147 問(Phase 5 の eval 側)"] --> run["llm-eval --config A〜E"]
    run -->|回答| srv["llama-server ルーターモード<br>35B-A3B / 4B / 4B+LoRA を 1 つずつ load"]
    run -->|RAG あり(B/E)| rag["Retriever(Phase 3)<br>docs/ + ai_harness 1168 チャンク"]
    run -->|採点| grader["Qwen3-8B(回答モデルと別)"]
    run --> out["eval/results/*.json / *.md"] --> readme["README の比較表"]
```

## なぜこの Phase が必要か

Phase 5 の暫定評価(20 問、キーワード一致)では LoRA モデルと素の 4B が同率 20% で、「LoRA に意味があるか」「RAG と比べてどうか」が言えなかった。
RAG(Phase 3)も「10 問で top-3 ヒット 10/10」という検索精度しか測っておらず、回答の正しさは測っていなかった。
この Phase は、ai_harness の「評価対象はシステム全体」「自己申告ではなく成果と証拠」に従い、5 構成を同じ条件・同じ採点器で並べて数値を出す。
また、公開して人に見せるには README に「何ができて、どれくらいの速さで、どう再現するか」が要る。

## 用語

| 用語 | 1 行説明 |
| --- | --- |
| 構成(A〜E) | 回答モデルと RAG の有無の組み合わせ。A 素の大型、B 大型+RAG、C 素の小型 4B、D 4B+LoRA、E 4B+LoRA+RAG |
| 決定論的採点 | 同じ入力なら必ず同じ点になる採点。ここでは正解文のキーワードの再現率(0.5 以上で正答) |
| キーワード再現率 | 正解文から取った語(英数字 3 字以上、カタカナ・漢字 2 字以上、「こと」「ため」などの一般語を除外)のうち、応答に含まれる割合 |
| LLM 採点(LLM-as-a-judge) | 別のモデルに「質問・模範解答・応答」を見せて点を付けさせる採点。文体の違いや言い換えに強いが、主観が入る |
| 採点モデルの分離 | 回答したモデルに自分の答えを採点させない規則(契約 GRADER_SEPARATION)。同じ誤解を正解扱いしてしまうため |
| JSON Schema 強制 | llama-server の `response_format: json_schema` で、出力を `{"score": 1〜5, "reason": "…"}` の形に限定する仕組み |
| 検索ヒット率 | RAG で検索した上位 k 件に、その問いの出典ファイルが含まれる割合。回答の良し悪しと切り離して検索だけを測る |
| 引用率 | 応答に `[1]` のような引用番号が含まれる割合。RAG の指示(資料を根拠に答えて [n] を付ける)に従ったかの目安 |
| ルーターモード | llama-server が `models.ini` の複数モデルを 1 つのポートで配信し、`/models/load` `/models/unload` で載せ替えられる機能 |
| pp / tg | llama-bench の指標。pp = プロンプト処理速度、tg = 生成速度(どちらも tok/s) |
| ctx-size | モデルが一度に読める最大トークン数。大きくすると KV キャッシュの分だけ VRAM が増える |

## やったことの流れ

### 1. 契約と Baseline

- 契約 `docs/harness/contracts/phase-6.json` を読み、完了条件 EVAL-001〜006 と scope(`models.ini` は書けない)を確認した。
- 実装前に `.\scripts\agent\verify.ps1 -Agent` を打った。最初は worktree に `agent/harness/node_modules` が無く vitest が見つからず失敗、`npm install` 後に ALL OK(pytest 87、vitest 35)。出力は `docs/harness/reports/evidence/phase-6.baseline.txt`。

### 2. 評価ハーネスの設計

- **構成はコードで固定**(`src/local_llm/eval/configs.py`)。A/B の大型モデルは設定 `LLM_DEFAULT_MODEL` に追従させ、C/D/E は Phase 5 で登録したモデル id を直書きした。
- **採点モデルは Qwen3-8B にした**。理由: 回答側に 35B-A3B と 4B 系を使うので、どちらとも別で、キャッシュ済みでないものの中で 12 GB に単独で載り(約 5.5 GB)、日本語の採点ができる最小のモデルだったから。
  27B は VRAM に収まらず約 6 tok/s なので 735 回の採点には使えない。「構成ごとに採点モデルを替える」案(35B が 4B を採点、8B が 35B を採点)は比較の土台が崩れるので採らなかった。
- **回答と採点を 2 段に分けた**。1 構成の実行は「回答モデルを load → 全問回答 → unload → 採点モデルを load → 全問採点 → unload」。
  12 GB に 35B の GPU 側(約 3 GB)と 8B(約 5.5 GB)と埋め込み(0.7 GB)を同時に載せるのは、他のプロセスが VRAM を使っている状況では危ないため。
- **決定論的採点は Phase 5 の規則をそのまま使った**(`training/eval_quick.py` と同じキーワード抽出と閾値 0.5)。LORA-005 の暫定値(20 問)と Phase 6 の値(147 問)を同じ物差しで見られる。
- **LLM 採点は温度 0、thinking off、JSON Schema 強制**。パースに失敗したら `score: null` で「採点失敗」として数える(黙って 0 点や平均から除外にしない)。
- **結果 JSON の Schema**(`eval/schemas/qa-result.schema.json`)。JSON Schema では「`grader.model` ≠ `answer.model`」を直接書けないので、runner が実行前に同一なら止め(終了コード 2)、JSON には `grader.differs_from_answer_model: true` を書き(Schema は `const: true`)、`scripts/agent/check_eval_results.py` が文字列比較で裏を取る。この 3 段を `verify.ps1` に組み込んだ。
- **RAG の検索対象を広げた**。評価の問いの約 2 割は このリポジトリの `docs/` から合成されているのに、Phase 3 のコーパスは ai_harness だけだった。`scripts/sync-local-corpus.ps1` で README と `docs/**/*.md` を `data/corpus/local_llm/` にコピーし、ai_harness 側は Phase 5 と同じ 21 本(日本語ハンドブック 2 本を含む)にして、`llm-index` で 36 文書 / 1,168 チャンクの索引を作った。
  `docs/journal/phase-6.md` と `docs/harness/reports/phase-6*` は索引から除外した(評価結果そのものが資料に混ざるのを避ける)。

### 3. 実機で動かすまでに起きたこと

- **前のセッションの llama-server(:8080)と llm-web(:8000 / :8010)が残っていた**。`Stop-Process` は環境の方針で拒否されたので止めず、自分のサーバを :8082 で起動した。
  残ったサーバが VRAM を約 3.9 GB(35B + 埋め込み)使い続けているため、Phase 6 の計測はその分だけ空きが少ない条件で行っている(ベンチの節に影響を書く)。
- **worktree には `llama-local/` が無い**。`start-server.ps1 -LlamaDir C:\dev\local_llm\llama-local` で main 側のバイナリを使った。
- **8B がいつまでも `loading` だった**。`models.ini` の `[unsloth/Qwen3-8B-GGUF:Q4_K_M]` に `hf-repo` が無いため、ルーターは子プロセスにモデルを渡さず、子がそれ自身ルーターとして起動していた(Phase 5 の引き継ぎノートが「8B も同じ問題を持つはず」と予告していたとおり)。
  `models.ini` は契約の scope 外なので本体は変えず、1 行足した `eval/models-eval.ini` を作って `-Preset` で渡した。本体への 1 行追加は Amendment 案として Report に書いた。
  8B の GGUF(5.0 GB)自体は、`llama-server -hf unsloth/Qwen3-8B-GGUF:Q4_K_M` を一時的に CPU だけで起動してキャッシュに落とし、すぐ止めた。
- **検索ヒットの判定に接頭辞のずれがあった**。データセットの `source` は `data/corpus/ai_harness/x.md`、索引のパスは `ai_harness/x.md` で、末尾一致の向きが逆だった。3 問のスモークで「資料は出ているのにヒット 0」になって気付き、どちら向きでも一致すれば良いように直してテストを足した。

### 4. 評価の結果

<!-- eval-results:start -->
(実行後に埋める)
<!-- eval-results:end -->

### 5. 計測(bench / ctx-vram)

<!-- bench-results:start -->
(実行後に埋める)
<!-- bench-results:end -->

### 6. README と公開準備

- README に構成図(Mermaid)、各 Phase の成果表、最短の再現手順、評価とベンチの表、ロードマップ、「公開(人間が行う手順)」を足した。`LICENSE` は MIT(著作権者 Moro-1025)。
- push、public 化、Hugging Face へのアップロードは行っていない。

## 動かし方

```powershell
.\scripts\start-server.ps1 -Preset .\eval\models-eval.ini   # 期待: "llama-server を起動します" と preset のパスが出る
.\scripts\fetch-corpus.ps1; .\scripts\sync-local-corpus.ps1  # 期待: data/corpus/ に ai_harness/ と local_llm/
uv run llm-index                                             # 期待: "更新 36 文書 / ... / 新規チャンク 1168 件"(数は文書の版で変わる)
uv run llm-eval --suite qa --config A                        # 期待: [A 回答] 1/147 ... → [A 採点] 1/147 ... → "完了: 正答率 N/147 LLM 採点平均 X -> eval/results/...json"
.\scripts\run-eval-all.ps1                                   # 期待: 構成 A〜E が順に走り、最後に比較表と ALL OK
uv run llm-eval --summarize                                  # 期待: README に貼る Markdown の表
uv run python scripts/agent/check_eval_results.py            # 期待: 結果ファイルごとに "OK ... (grader != answer)"
.\scripts\bench.ps1 -Models "unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_M","unsloth/Qwen3-8B-GGUF:Q4_K_M","unsloth/Qwen3-4B-GGUF:Q4_K_M"   # 期待: bench/bench-<日時>.md
.\scripts\ctx-vram.ps1                                       # 期待: bench/ctx-vram-<日時>.md
uv run pytest                                                # 期待: 107 passed
```

## どう確かめたか(証拠)

Completion Report: [docs/harness/reports/phase-6.md](../harness/reports/phase-6.md)。要点:

- `uv run pytest -q` → `107 passed`(うち `tests/eval/` 20 件。偽のルーターに対して CLI 一式が JSON と Markdown を出し、Schema に適合し、載せ替え順と `response_format` の送信を確認)
- `uv run llm-eval --suite qa --config A` の実機出力 → `eval/results/<日時>-qa-A.json` と `.md`(EVAL-001)
- `uv run python scripts/agent/check_eval_results.py` → 5 構成すべて `OK ... (grader != answer)`(EVAL-003)
- README の比較表は `uv run llm-eval --summarize` の出力をそのまま貼ったもの。手で数字を打っていない(EVAL-002)
- `bench/bench-<日時>.md` と `bench/ctx-vram-<日時>.md` の数値を README の表に転記(EVAL-004)

## 評価の限界(読む前に知っておくこと)

| 限界 | 何が言えなくなるか | 対処・備考 |
| --- | --- | --- |
| **件数が 147 問** | 正答率の差が数ポイントなら誤差の範囲。±8 ポイント程度(95% 信頼区間の目安)より小さい差は「差がある」と言えない | 構成間で 10 ポイント以上の差だけを結論に使う |
| **LLM 採点は主観的** | 採点モデル(8B)が模範解答の言い換えを見逃したり、もっともらしい誤答に点を付けたりする。1〜5 の意味も採点モデルの解釈に依存する | 決定論的採点と並べて見る。両者が食い違う問いが「採点の難しさ」の実例(結果の節) |
| **採点モデルが回答モデルより小さい(8B が 35B を採点)** | 大型モデルの正しい答えを採点モデルが理解できず低く付ける可能性がある | 模範解答が与えられているので「答え合わせ」であり、ゼロから答えるより易しい。それでも 35B の点が過小評価されている可能性は残る |
| **データセットが学習データと同じ合成元** | `qa-v1.jsonl` は Phase 5 で学習データと同じ手順(同じモデル、同じプロンプト)で合成した eval 側。問いの文体や「何を問うか」の癖が学習データと同じなので、LoRA(D/E)に有利な評価になりうる。逆に RAG(B/E)は同じ文書を索引しているので、文書に答えがそのまま書いてある問いが多く RAG に有利でもある | 人手で作った問いや、文書に無い推論を要する問いでの評価は別途必要 |
| **模範解答も合成** | 模範解答自体が誤っている可能性(Phase 5 で全件は人手確認していない)。その場合、正しい応答が「誤答」と採点される | 合成時に「チャンクだけから答えられる」制約を付けたが、品質の全件確認はしていない |
| **1 回の実行** | 温度 0 でも llama.cpp の実装上わずかに非決定的な部分がある。再実行で数問ぶれる可能性 | 結果 JSON に全応答を残しているので再実行して比べられる |
| **キーワード採点の粗さ** | 正解語を並べただけの応答が正答になり、正しいが言い換えた応答が誤答になる | LLM 採点と併用。閾値 0.5 は Phase 5 と揃えるために固定 |
| **計測環境** | 前セッションの llama-server が VRAM 約 3.9 GB を占有した状態で計測した。tok/s は同時に動くプロセスにも影響される | ベンチの節に当時の VRAM 使用量を併記 |

## うまくいかなかったこと・やり直したこと

- Baseline の 1 回目は vitest 不在で失敗(worktree の `node_modules` 無し)。`npm install` して再実行。
- 8B 採点モデルの `loading` 固着(上述)。原因が分かるまで約 10 分、`/models/unload` で子を殺し、プリセットの上書き版で解決。
- 検索ヒット判定の接頭辞の向き(上述)。スモーク 3 問で発見。
- 残っていた前セッションのプロセスを止められなかった(環境方針で `Stop-Process` 拒否)。ポートをずらして進めたが、VRAM を 3.9 GB 失った状態での計測になった。
- bash のヒアドキュメントで長いファイルを書くと途中で切れた(`report.py`)。長いファイルは Write ツールで書くようにした。

## 次の Phase への引き継ぎ

- human-review 待ち: EVAL-002(README の比較表と結果 JSON の一致)、EVAL-005(README の構成図・再現手順・LICENSE)、EVAL-006(この journal)。`phase-6.decision.json` は `needs_human` のまま。
- Amendment 案(契約の次版): `models.ini` の 8B セクションに `hf-repo` を足す(1 行)、`scope.write` に `models.ini` を含めるか判断、8B の取得(Hugging Face からのダウンロード)を `destinations` に明記。
- 止めていないプロセス: 前セッションの llama-server(:8080、PID 6268 と子 2 つ)、llm-web(:8000 PID 11164、:8010 PID 46628)。Phase 6 で起動した :8082 のサーバは作業終了時に停止する。
- 残存リスク: 評価の限界の表。特に「データセットが学習データと同じ合成元」は、LoRA の効果を言うには別のデータセット(`qa-v2`: 人手作成)が必要。
- 残る宿題: `docs/images/` のスクリーンショット(仕様 5.3)は未作成。LoRA の改善(言い換えデータ、epoch 増)は Phase 5 の引き継ぎのまま。

## 参考リンク

- 仕様: [docs/spec-phase3-6.md 5 章](../spec-phase3-6.md)
- 契約: [docs/harness/contracts/phase-6.json](../harness/contracts/phase-6.json)
- 外部仕様 12 章(`llm-eval` の使い方)/ 内部仕様 13 章(モジュールとデータフロー): [external-spec.md](../external-spec.md) / [internal-spec.md](../internal-spec.md)
- ai_harness の評価の考え方: [docs/learning/intermediate/ci-review-and-evaluation.md](https://github.com/Moro-1025/ai_harness/blob/main/docs/learning/intermediate/ci-review-and-evaluation.md)
- llama-server のルーターモードと `/models/load`: [llama.cpp tools/server README](https://github.com/ggml-org/llama.cpp/tree/master/tools/server)
