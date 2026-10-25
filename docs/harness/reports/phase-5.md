# Completion Report: Phase 5(LoRA ファインチューニング)

- 契約: [contracts/phase-5.json](../contracts/phase-5.json)(`contract:local-llm:phase-5`、version 1、hash `sha256:99cc64d9...`)
- 判定: [phase-5.decision.json](phase-5.decision.json)
- ジャーナル: [docs/journal/phase-5.md](../../journal/phase-5.md)
- ブランチ: `claude/phase5-lora-35367e`(Baseline コミット `df8c706`、成果コミットは末尾)
- 実行環境: Windows 11 + WSL2 Ubuntu 24.04、RTX 5070 12 GB、llama.cpp b11146(Windows)、unsloth 2026.10.8 / torch 2.14.1+cu130(WSL)

## Baseline(実装前)

```
.\scripts\agent\verify.ps1
=== contracts schema === OK(phase-3/4/5/6.json)
=== git diff --check === OK
=== pytest === 40 passed in 3.20s
commit: df8c706 / ALL OK
```

## Targeted Verification(実装後)

```
.\scripts\agent\verify.ps1 -Training
=== contracts schema === OK
=== git diff --check === OK
=== pytest === 40 passed
=== training tests (WSL) === 9 passed
ALL OK
```

## Criterion ごとの結果

| ID | 条件 | 結果 | 証拠(要点) |
| --- | --- | --- | --- |
| LORA-001 | `setup-wsl.sh` 後に `torch.cuda.is_available()` が True | **pass** | `setup-wsl.sh` の出力末尾: `torch 2.14.1+cu130 cuda 13.0` / `torch.cuda.is_available(): True` / `device: NVIDIA GeForce RTX 5070 VRAM: 11.94 GiB capability: sm_120` |
| LORA-002 | `qa.jsonl` ≥ 300 組、各行 question/answer/source、`qa.meta.json` にプロンプト・モデル id・日時 | **pass** | `wc -l training/data/qa.jsonl` = 1471。`qa.meta.json`: model `unsloth/Qwen3.6-35B-A3B-GGUF:Q4_K_M`、started 2026-10-25T02:13:52Z、finished 03:14:29Z、counts raw 1482 / kept 1471 / dropped {short_answer 1, no_source_term 8, duplicate 2}。`training/run_tests.sh`: 9 passed(件数・schema・重複・split の整合) |
| LORA-003 | `training/runs/<日時>/` に loss 推移、最終 eval loss < 最初の eval loss | **pass** | `training/runs/20261025-121533/summary.json`: eval_loss_first 3.906 → eval_loss_last 1.678(`eval_loss_decreased: true`)、166 step / 2 epoch / 290 秒、VRAM peak reserved 5.91 GiB。`loss.md` に 10 step ごとの表 |
| LORA-004 | GGUF が `models.ini` に登録され、llama-server 経由で CLI から 1 往復 | **pass** | `export_gguf.sh` → `C:\dev\local_llm\models\qwen3-4b-localllm-lora-Q4_K_M.gguf`(2,497,280,832 byte)。`start-server.ps1 -Port 8081` 起動後 `/v1/models` に `local/qwen3-4b-localllm-lora`。`uv run llm-chat --model local/qwen3-4b-localllm-lora --base-url http://127.0.0.1:8081 --no-think` で 1 往復: `prompt 27 tok / completion 127 tok, 96.6 tok/s, 1.4s`(`logs/chat-20261025-123429.jsonl`) |
| LORA-005 | eval 20 問の簡易正答率で LoRA > 素の 4B(required=false、暫定) | **(下の節)** | `training/eval_quick.py`(キーワード一致 50% 以上で正答) |
| LORA-006 | `docs/journal/phase-5.md` がテンプレート全節を満たし、VRAM 実測とモデル選定理由がある | **pass**(human-review 待ち) | 全 9 節あり。「3. 学習」に VRAM 見積もり 4.03 GiB / 実測 4.14 (allocated) / 5.91 (reserved) GiB と、Qwen3-4B を選んだ理由(見積もりと smoke 実測が 12 GB に対して十分小さく OOM 0 回) |

### LORA-005 の結果(暫定)

**fail**(required=false)。正答率は同率で「上回る」を満たさない。キーワード再現率の平均はわずかに高い。

```
uv run python training/eval_quick.py --base-url http://127.0.0.1:8081 --unload-between \
    --models local/qwen3-4b-localllm-lora unsloth/Qwen3-4B-GGUF:Q4_K_M
```

| モデル | 正答 / N | 正答率 | キーワード再現率(平均) |
| --- | --- | --- | --- |
| local/qwen3-4b-localllm-lora | 4 / 20 | 20% | 0.36 |
| unsloth/Qwen3-4B-GGUF:Q4_K_M(素) | 4 / 20 | 20% | 0.30 |

- 条件: `eval/datasets/qa-v1.jsonl` の先頭 20 問、temperature 0、thinking off、max_tokens 400、正解文のキーワード(英数字 3 字以上 / カタカナ・漢字 2 字以上、一般語を除外)の 50% 以上が応答に含まれれば正答。
- 詳細: `training/runs/eval-20261025-124722.json`(各問の応答・ヒットしたキーワード)。
- 所見: LoRA モデルは `FastAPI`、`scripts/` 配下のファイル名など**文書の語彙**を使って答えるが、ファイル名や手順の**事実**は間違えることが多い(例: `ctx-vram.ps1` を `scripts/eval/ctx_size_vram.py` と答える)。素のモデルは一般論(`make test`、`Node.js`)で答える。どちらも 20% なので、現状の LoRA は「知識の注入」としては不十分。Phase 6 で RAG と比較し、LoRA 側は epoch 増や同一事実の言い換えデータ(1 チャンク 3 組 → 多様な問い方)を試す。

## 制約(constraints)の遵守

| ID | 状態 |
| --- | --- |
| NO_PUSH | push、公開設定変更、HF アップロードは行っていない。モデルカードの下書きのみ(`training/export/README.md`) |
| NO_SECRETS | API キー等は無し。`.env` 不使用 |
| JOURNAL | `docs/journal/phase-5.md` を実装と同時に記述 |
| REPORT | 本ファイルと `phase-5.decision.json` |
| AMEND_NOT_EXPAND | scope.write 外のファイルは変更していない(下の「Amendment 案」に、範囲の解釈が必要だった点を記録) |
| SEPARATE_ENV | torch 等は WSL の `~/venvs/local_llm-training` のみ。ルートの `pyproject.toml` / `uv.lock` は無変更 |
| EVAL_SPLIT | `split.py`(seed 42)で 1324 / 147 に分割。eval 側を `eval/datasets/qa-v1.jsonl` に保存し学習には使っていない |
| VRAM_GUARD | 学習前見積もり 4.03 GiB、`train_lora.py` は見積もり 11 GiB 超で中止する。実測ピーク 5.91 GiB。OOM 0 回 |

## 未実施・制限

- WSL から Windows の llama-server への直接到達は NAT のため不可だった(gateway / nameserver / mDNS すべて失敗)。合成は Windows 側 uv から実行した。`-Expose` 起動での到達は**未確認**。
- LORA-005 は Phase 6 の採点器ではなく、キーワード一致の簡易採点。n=20 で統計的な差は語れない。
- xformers が torch 2.14 と不整合で読み込めず、unsloth は SDPA にフォールバック(学習は完走)。
- `verify.ps1 -Training` は WSL の venv が無い環境では失敗する(`run_tests.sh` がメッセージを出して exit 1)。
- Phase 3/4 のセッションが同時に 8080 で llama-server を使っていたため、Phase 5 の e2e は 8081 で行った。

## 残存リスク

| リスク | 深刻度 | 備考 |
| --- | --- | --- |
| LoRA モデルの回答が事実と異なる(ハルシネーション) | medium | eval loss は下がったが、`models.ini` の `[*]` の説明など誤答を確認。Phase 6 で RAG と比較して用途を決める |
| 合成 Q&A の品質が合成モデル(Qwen3.6-35B-A3B)に依存 | low | 質問に出典語を強制し、答え 20 字未満を除外したが、内容の正確さは人手で全件は見ていない |
| `models.ini` の絶対パスが `C:\dev\local_llm\models\` 固定 | low | 別の場所に clone すると要修正。README に記載 |
| WSL 側の環境(venv、gcc、llama.cpp clone)はリポジトリ外 | low | `setup-wsl.sh` で再構築できる。`requirements.lock.txt` で版を固定 |
| xformers の不整合 | low | 性能のみ。`pip uninstall xformers` で警告は消える |

## Amendment 案(契約の解釈が必要だった点、次版で明記したい)

1. `scope.destinations` に raw.githubusercontent.com(ai_harness の取得)と github.com(llama.cpp の shallow clone、`convert_hf_to_gguf.py` のため)が無い。親セッションの指示に従い取得したが、契約上は明記されていない。→ destinations に追加する。
2. `scope.forbidden` に `**/*.gguf` があるが、`export_gguf.sh` は `models/*.gguf` を**生成**する(編集ではない、.gitignore 済み)。→ 「リポジトリ管理下の .gguf を変更しない」の意図を明記する。
3. GGUF の配置先 `C:\dev\local_llm\models\` は worktree ではなく main の checkout 配下(git 管理外)。models.ini の絶対パスを main 取り込み後に有効にするため。
4. WSL への `build-essential` 導入(`wsl -u root`)はリポジトリ外のシステム変更。契約の capability_profile には含まれていない。→ `setup-wsl.sh` の前提として契約に書く。

## Rollback

- ブランチを捨てる(`git branch -D claude/phase5-lora-35367e`)。main には何も入っていない。
- 生成物: `C:\dev\local_llm\models\qwen3-4b-localllm-lora-Q4_K_M.gguf`、`training/runs/`、`training/data/cache/`、`data/corpus/ai_harness/`(すべて git 管理外)を削除。
- WSL: `rm -rf ~/venvs/local_llm-training ~/llama.cpp ~/.cache/huggingface/hub/models--unsloth--Qwen3-4B`、`apt-get remove build-essential`(任意)。

## コミット

COMMIT-PLACEHOLDER
