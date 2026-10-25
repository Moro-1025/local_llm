# Phase 5: LoRA ファインチューニング(WSL)

## この Phase で作ったもの(1 分で)

このリポジトリの `docs/` と ai_harness の Markdown から Q&A を自動合成し、それを教材にして小型モデル Qwen3-4B に
QLoRA(4bit の本体に小さな追加重みだけを学習させる方法)をかけた。学習は WSL Ubuntu 上の unsloth で行い、
できた重みをベースに合体(マージ)して GGUF に変換、`models.ini` に `local/qwen3-4b-localllm-lora` として登録した。
llama-server から普通のモデルと同じように会話できる。

```mermaid
flowchart LR
    docs["docs/ + ai_harness の md"] -->|llama-server で合成| qa["qa.jsonl(Q&A)"]
    qa -->|9:1| tr["train"] & ev["eval → eval/datasets/qa-v1.jsonl"]
    tr -->|unsloth QLoRA(WSL)| ad["LoRA アダプタ"]
    ad -->|マージ → GGUF Q4_K_M| gguf["models/*.gguf"] --> ini["models.ini"] --> srv["llama-server → llm-chat"]
```

## なぜこの Phase が必要か

Phase 3 の RAG は「文書を検索して渡す」ことで知識を補う。Phase 5 は同じ知識を「モデルの重みに入れる」別のやり方で、
Phase 6 で「RAG あり」「LoRA のみ」「素のモデル」を同じ質問で比べるための材料になる。
また、12 GB の GPU でどこまで学習できるかを実測で知ることが目的の一つ。

## 用語

| 用語 | 1 行説明 |
| --- | --- |
| ファインチューニング | 学習済みモデルに追加のデータを学習させて振る舞いを変えること |
| LoRA | 元の重みは凍結し、低ランクの小さな行列(アダプタ)だけを学習する省メモリな手法 |
| QLoRA | 本体を 4bit に量子化してメモリを減らした上で LoRA を行う。12 GB VRAM で 4B モデルが学習できる理由 |
| unsloth | LoRA 学習を速く・省メモリにするライブラリ。Triton カーネルを実行時にコンパイルするので gcc が要る |
| r / alpha | LoRA の行列のランク(r)と倍率(alpha)。大きいほど表現力が増えメモリも増える。今回は r=16, alpha=16 |
| eval loss | 学習に使っていないデータで測った損失。これが下がれば「丸暗記」ではなく汎化している |
| GGUF | llama.cpp が読むモデル形式。学習結果(safetensors)はこれに変換しないと llama-server で使えない |
| Q4_K_M | GGUF の 4bit 量子化方式の一つ。サイズと品質のバランスが良く、このプロジェクトの標準 |
| WSL | Windows 上で Ubuntu を動かす仕組み。unsloth と bitsandbytes が Linux 前提なので学習だけここで行う |
| 評価汚染 | 評価用の質問が学習データに混ざり、スコアが見かけ上良くなること。eval を先に切り離して防ぐ |

## やったことの流れ

### 1. 環境(WSL)

- WSL の Ubuntu 24.04 には Python 3.12 はあるが pip も venv も gcc も無かった。`uv` を入れ、uv 管理の Python 3.12 で venv を作った(`ensurepip` の問題を避けられる)。
- gcc は unsloth(Triton)が実行時に必要。`sudo` はパスワードが要るので `wsl -d Ubuntu -u root -- apt-get install -y build-essential` で入れた。
- venv は `/mnt/c` ではなく WSL 側の `~/venvs/local_llm-training` に置いた。/mnt/c 上の Python パッケージは読み込みが数倍遅い。
- torch は PyPI の Linux ホイール(CUDA 13.0 同梱)をそのまま使った。RTX 5070(sm_120)は CUDA 12.8 以降が必要で、ドライバは CUDA 13.2 対応なので問題ない。
- xformers は torch 2.14 と合うビルドが無く読み込めない旨の警告が出るが、unsloth は PyTorch 標準の SDPA に自動で切り替える。学習速度への影響は小さいので放置した。

### 2. データ合成

- llama-server(Windows 側、Qwen3.6-35B-A3B)に「このチャンクだけから答えられる Q&A を 3 組」を JSON Schema 指定で頼んだ。JSON Schema を使うと形が崩れずパース失敗がほぼ無い。
- **WSL から Windows の llama-server には届かなかった**。WSL は NAT モードで、Windows 側は 127.0.0.1 にしかバインドしていないため。試した到達方法と結果:

| 方法 | 結果 |
| --- | --- |
| 既定ゲートウェイ `172.25.80.1:8080`(`ip route show default`) | 接続不可(サーバが 127.0.0.1 のみ) |
| `/etc/resolv.conf` の nameserver `10.255.255.254:8080` | 接続不可 |
| `MORO5070.local:8080`(mDNS) | 名前解決不可 |

  合成スクリプトは httpx しか使わないので、**Windows 側の uv 環境から実行**した(`uv run python training/data/build_dataset.py`)。
  WSL から届かせたいときは `start-server.ps1 -Expose`(0.0.0.0 バインド)で起動して既定ゲートウェイの IP を使う(未確認)。
- フィルタは決定論的: 答え 20 字未満、質問 10 字未満、質問に出典語(チャンク内の英数字 3 字以上の語、またはカタカナ/漢字 2 字以上)が無いもの、正規化した質問の重複を落とす。
- 応答はチャンクごとに `training/data/cache/` に残し、再実行時に再利用する(途中で止めても続きから)。

### 3. 学習

- **モデル選定**: 第一候補の `unsloth/Qwen3-4B`(4bit)をそのまま採用した。理由は、学習前の見積もり(4bit 重み 2.5 GiB + 活性 + 余裕 ≒ 4 GiB)が 12 GB に対して十分小さく、3 ステップの動作確認で実測ピーク 4.5 GiB だったため。OOM は 1 回も出なかったので Qwen3-1.7B への切り替えは不要だった。
- **設定**(`training/config.yaml`): r=16, alpha=16, dropout 0, 全線形層(q/k/v/o/gate/up/down)、lr 2e-4(linear、warmup 5)、2 epoch、batch 4 × 勾配累積 4 = 実効 16、max_seq_length 2048、adamw_8bit、loss は assistant 応答のみ(`train_on_responses_only`)。
- **データ形式**: Qwen3 の chat template に system / user / assistant を渡し、assistant の先頭に空の `<think>\n\n</think>\n\n` を付けた。推論時に thinking を切ったときの出力形と一致させるため。
- **結果**(`training/runs/20261025-121533/summary.json`):

| 項目 | 値 |
| --- | --- |
| 学習データ / 評価データ | 1,324 件 / 147 件 |
| ステップ数 | 166(2 epoch) |
| 学習時間 | 290 秒(約 9.2 サンプル/秒) |
| train loss(step 5 → 165) | 3.60 → 1.58 |
| eval loss(step 0 → 166) | **3.91 → 1.68** |
| VRAM ピーク(allocated / reserved) | 4.14 / 5.91 GiB |
| 学習パラメータ | 33.0M / 4,055M(0.81%) |

loss の推移(`loss.md` を転記、eval は 10 ステップごと):

| step | epoch | train loss | eval loss |
| --- | --- | --- | --- |
| 0 | 0.00 |  | 3.9062 |
| 10 | 0.12 | 2.4964 | 2.1948 |
| 20 | 0.24 | 2.0082 | 2.0046 |
| 40 | 0.48 | 1.9041 | 1.8656 |
| 60 | 0.73 | 1.8093 | 1.8018 |
| 80 | 0.97 | 1.7656 | 1.7567 |
| 100 | 1.21 | 1.5712 | 1.7222 |
| 120 | 1.45 | 1.4359 | 1.7080 |
| 140 | 1.69 | 1.4973 | 1.6859 |
| 160 | 1.93 | 1.4306 | 1.6782 |
| 166 | 2.00 |  | 1.6780 |

eval loss は単調に下がり、2 epoch 目の後半で平らになっている。train と eval の差(1.43 vs 1.68)は小さく、過学習の兆候は無い。3 epoch 目を足しても伸びは小さいと見て 2 epoch で止めた。

### 4. 変換と登録

- `training/export_gguf.sh runs/20261025-121533` の 3 段: unsloth でアダプタをベースに 16bit マージ(`export/merged/`、7.7 GB)→ llama.cpp の `convert_hf_to_gguf.py` で F16 GGUF → Windows 側の `llama-quantize.exe` で Q4_K_M(2.4 GB、量子化 47 秒)。WSL から Windows の .exe を直接呼べる(interop)ので、量子化は既存の Windows バイナリを使い、WSL で llama.cpp をビルドしなかった。
- unsloth 自身にも `save_pretrained_gguf` があるが、内部で llama.cpp を clone してビルドする(cmake と時間が要る)。既に手元に `llama-quantize.exe` があるので使わなかった。
- GGUF は `C:\dev\local_llm\models\qwen3-4b-localllm-lora-Q4_K_M.gguf` に置いた。`*.gguf` は .gitignore 済み。
- `models.ini` には `[local/qwen3-4b-localllm-lora]` に `model = <絶対パス>` を書いた。llama-server のルーターモードは「セクション名が既存モデルに一致しなければ `model =` か `hf-repo =` が必須」で、パスは絶対指定が推奨(README「Using multiple models」)。相対パスはサーバの CWD 基準になり、`start-server.ps1` をどこから起動したかで変わるため避けた。比較用に `[unsloth/Qwen3-4B-GGUF:Q4_K_M]` も追記した(既存セクションは変更していない)。
- 起動して `/v1/models` に `local/qwen3-4b-localllm-lora` が出ること、`uv run llm-chat --model local/qwen3-4b-localllm-lora` で 1 往復できることを確認した(96.6 tok/s、完全に GPU に載る)。

| 確認 | 結果 |
| --- | --- |
| `/v1/models` に `local/qwen3-4b-localllm-lora` | 出る |
| `llm-chat` 1 往復(thinking off) | 127 tok / 1.4 秒 / 96.6 tok/s |
| 応答の内容 | ドキュメント風の日本語で答えるが、`[*]` セクションの説明などは**事実と違う**(下の「うまくいかなかったこと」) |

## 動かし方

[training/README.md](../../training/README.md) に手順を書いた。要約:

```powershell
wsl -d Ubuntu -u root -- apt-get install -y build-essential
wsl -d Ubuntu -- bash -lc "cd /mnt/c/dev/local_llm/training && ./setup-wsl.sh"          # → torch.cuda.is_available(): True
.\scripts\start-server.ps1
bash training/data/fetch_corpus.sh
uv run python training/data/build_dataset.py                                             # → kept N / raw M
uv run python training/data/split.py                                                     # → train / eval
wsl -d Ubuntu -- bash -lc "cd /mnt/c/dev/local_llm/training && source ~/venvs/local_llm-training/bin/activate && python train_lora.py"
wsl -d Ubuntu -- bash -lc "cd /mnt/c/dev/local_llm/training && ./export_gguf.sh runs/<日時>"
.\scripts\start-server.ps1
uv run llm-chat --model local/qwen3-4b-localllm-lora
```

## どう確かめたか(証拠)

Completion Report: [docs/harness/reports/phase-5.md](../harness/reports/phase-5.md)、判定: `phase-5.decision.json`。要点:

| 確かめ方 | 打つコマンド | こう出る |
| --- | --- | --- |
| 環境(LORA-001) | `wsl -d Ubuntu -- bash -lc "cd /mnt/c/dev/local_llm/training && ./setup-wsl.sh"` | 末尾に `torch.cuda.is_available(): True` と `RTX 5070 VRAM: 11.94 GiB` |
| データ(LORA-002) | `.\scripts\agent\verify.ps1 -Training` | `training tests (WSL)` が `9 passed`(件数 ≥ 300、schema、重複なし、split 整合) |
| 学習(LORA-003) | `cat training/runs/20261025-121533/summary.json` | `eval_loss_first 3.906`、`eval_loss_last 1.678`、`eval_loss_decreased: true` |
| 登録と往復(LORA-004) | `curl http://127.0.0.1:8081/v1/models` → `uv run llm-chat --model local/qwen3-4b-localllm-lora --base-url http://127.0.0.1:8081 --no-think` | 一覧に `local/qwen3-4b-localllm-lora`、1 往復で `96.6 tok/s` |
| 暫定評価(LORA-005) | `uv run python training/eval_quick.py --models local/qwen3-4b-localllm-lora unsloth/Qwen3-4B-GGUF:Q4_K_M` | 下の表 |

暫定評価(20 問、キーワード一致 50% 以上で正答):

| モデル | 正答 / N | 正答率 | キーワード再現率(平均) |
| --- | --- | --- | --- |
| LoRA あり(`local/qwen3-4b-localllm-lora`) | 4 / 20 | 20% | 0.36 |
| LoRA なし(`unsloth/Qwen3-4B-GGUF:Q4_K_M`) | 4 / 20 | 20% | 0.30 |

**同率で「上回る」は未達(LORA-005 は fail、ただし required=false)**。LoRA モデルは文書の語彙(FastAPI、`scripts/` のファイル名)で答えるようになったが、ファイル名や手順の細部は間違える。素のモデルは一般論で答える。eval loss が下がったのに正答率が変わらないのは、「文体・語彙」の学習と「事実」の記憶が別物であることを示している。

## うまくいかなかったこと・やり直したこと

- **WSL から llama-server に届かない**(上の表)。合成は Windows 側の uv で実行することにした。WSL 側で動かす必要があるのは torch を使う学習・変換だけなので、この分担で問題は無い。
- **gcc が無く unsloth が使えない**。`sudo` はパスワードが要るので `wsl -d Ubuntu -u root -- apt-get install -y build-essential` で入れた。`setup-wsl.sh` は gcc が無いときにこのコマンドを案内して止まる。
- **xformers が torch 2.14 と合わない**。PyPI の xformers 0.0.35 は torch 2.10 向けビルドで読み込めず、unsloth が SDPA にフォールバックする警告が出る。学習は問題なく通ったので放置。気になるなら `pip uninstall xformers`。
- **`verify.ps1 -Training` が worktree で動かない**。WSL のパス `/mnt/c/dev/local_llm/training` が固定されていたので、`wslpath -u` でリポジトリのパスから変換するようにした。さらに Windows のパスの `\` が WSL 側の bash に食われて `C:devlocal_llm...` になる問題があり、`/` に置換してから渡すよう直した(2 回目で通った)。
- **ポート 8080 の衝突**。Phase 3/4 のセッションが同時に 8080 で llama-server を起動していたので、Phase 5 の確認は `start-server.ps1 -Port 8081` で行い、`--base-url http://127.0.0.1:8081` を渡した。
- **Git Bash 経由の CLI 入力が文字化け**。`echo "日本語" | uv run llm-chat` を Git Bash から打つと入力が cp932 で渡り、ログも応答も化けた。PowerShell で `[Console]::InputEncoding/OutputEncoding` を UTF-8 にしてやり直したら正常。モデル側の問題ではなかった(API 直叩きでは最初から正常な日本語)。
- **unsloth が `unsloth_compiled_cache/` をリポジトリ直下に作る**。`.gitignore` に追加した。
- **応答の中身はまだ怪しい**。eval loss は下がったが、`models.ini` の `[*]` について聞くと存在しない項目を列挙するなど、1 回の学習では「文書の文体」は学べても「事実」の定着は弱い。合成 Q&A が 1 チャンクあたり 3 組で、同じ事実を言い換えて何度も見せていないことが一因と考えている。Phase 6 の評価で RAG と比べる。

## 次の Phase への引き継ぎ

- **使うファイル**: `eval/datasets/qa-v1.jsonl`(147 問、学習未使用)、`models.ini` の `local/qwen3-4b-localllm-lora` と `unsloth/Qwen3-4B-GGUF:Q4_K_M`、`training/eval_quick.py`(暫定採点のロジック。Phase 6 の採点器に置き換える)。
- **未完了**: LORA-005(正答率で素のモデルを上回る)。Phase 6 の採点器(決定論 + LLM 採点)で「RAG あり / LoRA のみ / 素」を同じ 147 問で比べる。
- **LoRA を改善するなら**: (1) 同じ事実を複数の問い方で聞く Q&A を増やす(今は 1 チャンク 3 組)、(2) 3 epoch にする(eval loss はまだ下がり続けていた)、(3) r=32。いずれも VRAM 6 GiB 前後で収まる見込み(未確認)。
- **残存リスク**: LoRA モデルのハルシネーション(medium)。`models.ini` の絶対パス依存(low)。WSL 環境はリポジトリ外(`setup-wsl.sh` で再構築可)。
- **Human Gate 待ち**: Hugging Face へのアダプタ/GGUF のアップロード(`training/export/README.md` がモデルカードの下書き)。git push。

## 実測まとめ

| 項目 | 値 |
| --- | --- |
| Q&A 合成 | 494 チャンク → 1,482 組 → フィルタ後 1,471 組、61 分(Qwen3.6-35B-A3B、約 7.4 秒/チャンク) |
| 学習 | Qwen3-4B 4bit QLoRA、1,324 件 × 2 epoch = 166 step、290 秒 |
| VRAM(学習) | ピーク 4.14 GiB allocated / 5.91 GiB reserved(12 GB 中) |
| eval loss | 3.91 → 1.68 |
| 変換 | マージ 7.7 GB(F16)→ Q4_K_M 2.4 GB、量子化 47 秒 |
| 推論(LoRA GGUF) | 96.6 tok/s(thinking off、全層 GPU) |
| 暫定正答率 | LoRA 20% / 素 20%(n=20) |

## 参考リンク

- unsloth: https://github.com/unslothai/unsloth
- llama.cpp server の preset(`--models-preset`)と `model =` によるローカルパス指定: https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
- ai_harness: https://github.com/Moro-1025/ai_harness
