# Completion Report: Phase 6(評価・計測・公開準備)

- 契約: [contracts/phase-6.json](../contracts/phase-6.json)(`contract:local-llm:phase-6`、version 1、hash `sha256:10203f9f...62c51e`)
- 判定: [phase-6.decision.json](phase-6.decision.json)(**status `needs_human`**。EVAL-002 / 005 / 006 は human-review 待ちで `required_criteria_complete=false`)
- 検証結果: `phase-6.EVAL-001.verification.json` 〜 `phase-6.EVAL-006.verification.json`
- ジャーナル: [docs/journal/phase-6.md](../../journal/phase-6.md)
- ブランチ: `claude/phase6-eval-cde1c2`(base main `16f9a6f`)。コミット: `5fd4ed9`(評価ハーネス)→ `f0dba7b`(README / LICENSE / 仕様 / journal 下書き / 評価用プリセット)→ `899664d`(評価結果 5 構成)→ 本 Report を含む docs コミット(`git log -1`)
- 実行環境: Windows 11、RTX 5070 12 GB、llama.cpp b11146(`C:\dev\local_llm\llama-local`)、Python 3.12 + uv、PowerShell 5.1
- 外部送信: なし。push / 公開設定 / Hugging Face アップロードは行っていない(Human Gate `GATE_EXTERNAL_PUBLISH`)

## Baseline(実装前)

`docs/harness/reports/evidence/phase-6.baseline.txt`(commit `16f9a6f`):

```
.\scripts\agent\verify.ps1 -Agent
=== contracts schema === OK(phase-3/4/5/6.json、decision 3 件、verification 11 件)
=== git diff --check === OK
=== pytest === 87 passed in 3.63s
=== vitest (agent/harness) === 35 passed (4 files)
commit: 16f9a6f / ALL OK
```

(worktree に `agent/harness/node_modules` が無く vitest が見つからなかったので、`npm install` を実行してから再実行した。依存の追加ではない)

## Targeted Verification(実装後)

`docs/harness/reports/evidence/phase-6.verify.txt`(作業ツリー = 本 Report のコミット内容、`commit:` 行は直前の `899664d`):

```
.\scripts\agent\verify.ps1 -Agent
=== contracts schema === OK(phase-6 の decision 1 件 + verification 6 件を含む)
=== eval results schema === OK ×5(20261025-17{1641-A,3634-B,4748-C,5007-D,5230-E}.json、grader != answer)
=== git diff --check === OK
=== pytest === 107 passed in 3.61s(Phase 5 までの 87 + eval 20)
=== vitest (agent/harness) === 35 passed (4 files)
ALL OK
```

```
uv run python scripts/agent/check_contracts.py    # phase-6.json / phase-6.decision.json / phase-6.EVAL-00N.verification.json すべて OK
uv run python scripts/agent/check_eval_results.py  # 5 ファイル OK
```

## Criterion ごとの結果

| ID | 条件 | 結果 | 証拠(要点) |
| --- | --- | --- | --- |
| EVAL-001 | `uv run llm-eval --suite qa --config A` が `eval/results/` に JSON と Markdown を生成 | **pass** | pytest `tests/eval/test_runner_cli.py::test_cli_config_a_writes_json_and_markdown`(モックのルーター。JSON が Schema 適合、Markdown に構成名と採点モデル、載せ替え順 load→unload→load→unload)。実機: `eval/results/20261025-171641-qa-A.{json,md}`(147 問、2026-10-25 08:16〜08:36 UTC、53/147 正答) |
| EVAL-002 | 5 構成の結果がそろい、README の比較表の数値が JSON と一致 | **not_run(human-review 待ち)** | 結果 5 ファイル(A〜E、各 147 問、`llm_failed` 0)。README の表は `uv run llm-eval --summarize` の出力をそのまま貼った(下の表)。owner は同コマンドを再実行して README と見比べれば照合できる |
| EVAL-003 | 結果 JSON の `grader.model` ≠ `answer.model`(schema で検証) | **pass** | `eval/schemas/qa-result.schema.json`(`grader.differs_from_answer_model: const true`)+ `scripts/agent/check_eval_results.py` の文字列比較。5 ファイル OK。実行前の拒否は `test_cli_refuses_same_grader_and_answer_model`(終了コード 2、HTTP 呼び出し 0)。採点モデルは全構成 `unsloth/Qwen3-8B-GGUF:Q4_K_M` |
| EVAL-004 | README のベンチマーク表と ctx-size 表が `bench/` の結果で埋まっている | **pass**(self-check) | `bench/bench-20261025-175710.md`(5 モデル、pp512 / tg128)、`bench/ctx-vram-20261025-175920.md`(35B、8k/16k/32k)→ README「ベンチマーク」「ctx-size と VRAM」「普段使いのモデル」 |
| EVAL-005 | README に構成図、再現手順、各 Phase の成果、LICENSE(MIT) | **not_run(human-review 待ち)** | README: Mermaid 構成図、「各 Phase の成果」表(journal リンク付き)、「再現手順(最短)」、ロードマップ(Phase 6 完了・レビュー待ち)、「公開(人間が行う手順)」。`LICENSE`: MIT、Copyright (c) 2026 Moro-1025 |
| EVAL-006 | `docs/journal/phase-6.md` がテンプレート全節を満たし、評価の限界が書かれている | **not_run(human-review 待ち)** | 全節あり。「評価の限界」に件数(147 問 × 1 回)、LLM 採点の主観性、採点モデルと回答モデルの関係(8B が 35B を採点する非対称)、データセットが学習データと同じ合成元であること、を表で記載 |

### 評価結果(README に転記した表、`uv run llm-eval --summarize` の出力)

| 構成 | モデル | RAG | 正答率(決定論) | キーワード再現率 | LLM 採点(1〜5) | 引用率 | 検索ヒット top-3 | tok/s | 結果ファイル |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A | `unsloth/Qwen3.6-35B-A3B-GGUF:Q4_K_M` | なし | 53 / 147(36.0%) | 0.410 | 2.72 | 0.0% | - | 36.9 | `20261025-171641-qa-A.json` |
| B | `unsloth/Qwen3.6-35B-A3B-GGUF:Q4_K_M` | あり | 79 / 147(53.7%) | 0.536 | 3.82 | 91.2% | 77.5% | 16.7 | `20261025-173634-qa-B.json` |
| C | `unsloth/Qwen3-4B-GGUF:Q4_K_M` | なし | 27 / 147(18.4%) | 0.326 | 2.55 | 0.0% | - | 153.6 | `20261025-174748-qa-C.json` |
| D | `local/qwen3-4b-localllm-lora` | なし | 32 / 147(21.8%) | 0.346 | 2.58 | 0.0% | - | 152.5 | `20261025-175007-qa-D.json` |
| E | `local/qwen3-4b-localllm-lora` | あり | 98 / 147(66.7%) | 0.599 | 3.77 | 55.1% | 77.5% | 117.4 | `20261025-175230-qa-E.json` |

条件: `eval/datasets/qa-v1.jsonl` 147 問全件(`n=147`、`seed=42` を記録)、temperature 0、thinking off、max_tokens 400、RAG top_k 5、採点 Qwen3-8B(temperature 0、JSON Schema 強制、失敗 0 / 735)。所見は journal「4. 評価の結果」。

## 制約(constraints)の遵守

| ID | 状態 |
| --- | --- |
| NO_PUSH | push、公開設定変更、HF アップロード、外部送信なし。README の「公開(人間が行う手順)」に手順だけ書いた |
| NO_SECRETS | API キー等なし。`.env` 不使用。結果 JSON に入るのは質問・回答・パスのみ |
| JOURNAL | `docs/journal/phase-6.md` を実装と並行して記述(評価の限界、うまくいかなかったこと、引き継ぎ) |
| REPORT | 本ファイル、`phase-6.decision.json`、`phase-6.EVAL-001〜006.verification.json`、evidence 2 件 |
| AMEND_NOT_EXPAND | scope.write 外は変更していない(`models.ini` はコピーで回避。下の Amendment 案) |
| GRADER_SEPARATION | runner が実行前に拒否(`GraderSeparationError` → 終了コード 2)、結果 Schema が `differs_from_answer_model: true` を要求、`check_eval_results.py` が文字列比較。採点 8B、回答 35B / 4B / 4B-LoRA |
| DATASET_VERSIONED | `eval/datasets/qa-v1.jsonl`(Phase 5 の split、無変更)。`load_dataset` は `-v<N>.jsonl` 以外を拒否し、結果に `sha256` と `version` を記録 |

Human Gate: `GATE_EXTERNAL_PUBLISH` は発火させていない。`GATE_NEW_DEPENDENCY`: 新規依存なし(`jsonschema` と `respx` は既存の dev 依存、`pyproject.toml` の変更は `llm-eval` エントリポイント 1 行)。

## 未実施・制限

- EVAL-002 / 005 / 006 の human-review は未実施。writer セッションは pass を付けない。
- EVAL-001 / 003 / 004 の自動検証は writer セッション自身の self-check(`independent_from_writer: false`)。
- 評価は 1 回のみ。temperature 0 でも完全に決定的ではなく、再実行で数問ぶれる。C と D の差(3.4 pt)は有意とは言えない。
- 27B(`unsloth/Qwen3.8-27B-GGUF:UD-Q4_K_M`)は評価の 5 構成に含めていない(契約どおり。ベンチのみ)。
- `scripts/bench.ps1` は PowerShell 5.1 + `$ErrorActionPreference = "Stop"` で llama-bench の stderr 出力が NativeCommandError になり 1 モデル目で止まった。外部コマンド呼び出しだけ `Continue` にして修正し、ローカル GGUF パスも受け付けるようにした(scope.write 内の `scripts/**`)。
- ベンチと ctx-vram の「起動前 VRAM」は残ったプロセスの分(1.7〜3.4 GB)を含む。tok/s への影響は小さいが、空き VRAM の絶対値は本来より小さい。

## 残っているプロセス(人の手で停止が必要)

| ポート | プロセス | 由来 | 状態 |
| --- | --- | --- | --- |
| 8080 | llama-server ルーター(PID 6268、子 12844 / 28232) | Phase 3/4 のセッション | `Stop-Process` が実行環境のポリシーで拒否され、残っている |
| 8000 | llm-web(PID 11164) | Phase 3 のセッション | 同上 |
| 8010 | llm-web(PID 46628) | Phase 4 のセッション | 同上 |
| 8082 | llama-server ルーター(本セッション、`-Preset .\eval\models-eval.ini`) | Phase 6 | セッション終了時に停止(停止できなければ最終報告に記載) |

停止方法: `Get-NetTCPConnection -LocalPort 8080,8000,8010 -State Listen | Select OwningProcess` で PID を確認し `Stop-Process -Id <PID> -Force`(llama-server は子プロセスも)。

## 残存リスク

| リスク | 深刻度 | 備考 |
| --- | --- | --- |
| 評価が 1 データセット × 1 回 | medium | 数 pt の差は語れない。seed / n を記録しているので部分集合での再実行は可能 |
| LLM 採点が Qwen3-8B 1 モデルの判断 | medium | B で「キーワード外れ・LLM 4 以上」32 問。決定論の採点と並べて読む |
| データセットが LoRA の学習データと同じ合成元 | medium | D / E に有利な方向のバイアスの可能性。他の文書で測った値ではない |
| 自動検証が writer の self-check | low | 独立検証は未実施 |
| 8B GGUF を huggingface.co から取得 | low | `scope.destinations` は 127.0.0.1 のみ。モデル取得は Phase 0〜5 と同じ扱いで行ったが明示の許可は無い(Amendment 案 2) |
| 残ったプロセス分の VRAM | low | 計測条件として journal に記載 |

## Amendment 案(契約の変更は行っていない)

1. **`models.ini` の `[unsloth/Qwen3-8B-GGUF:Q4_K_M]` に `hf-repo = unsloth/Qwen3-8B-GGUF:Q4_K_M` を 1 行足す。** 無いとルーターが子にモデルを渡さず `loading` のまま戻らない(Phase 3 の 35B と同じ原因、Phase 5 の引き継ぎノートが予告)。`models.ini` は Phase 6 の scope.write に無いので、1 行足したコピー `eval/models-eval.ini` を作り `start-server.ps1 -Preset` で渡した。次版の契約で `models.ini` を scope.write に入れるか、本体にこの 1 行を入れる。
2. **`scope.destinations` に huggingface.co を加える**(採点モデル 8B の GGUF 取得。`llama-server -hf` を一時起動して 5,027,784,512 byte をキャッシュに落とし、すぐ停止)。
3. **残ったプロセスの扱い**: 前セッションのサーバ停止が実行環境のポリシーで拒否される前提で、契約に「別ポートで起動し、残存プロセスを Report に書く」を明記する(Phase 4 / 5 / 6 で 3 回続いた)。
4. **`scripts/**` の既存スクリプト修正**は scope.write 内だが、Phase 0 の成果物(`bench.ps1`)を Phase 6 で直した。意図どおりか owner に確認。

## Rollback

- ブランチを捨てる(`git branch -D claude/phase6-eval-cde1c2`)。main には何も入っていない。
- 生成物(git 管理外): `data/corpus/local_llm/`(`sync-local-corpus.ps1` のコピー)、`data/chat.db` の索引(36 文書 / 1168 チャンク。`uv run llm-index` で再構築可)、`%USERPROFILE%\.cache\huggingface\hub\models--unsloth--Qwen3-8B-GGUF`(5.0 GB)。
- `eval/results/`、`bench/` はブランチ内のファイルのみ。
