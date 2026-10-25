# qwen3-4b-localllm-lora — モデルカード(下書き)

Hugging Face に上げるときの README の下書き。**アップロードは人間が行う(Human Gate)**。このフォルダの `merged/` と `*.gguf` は git 管理外。
数値は `training/runs/<日時>/summary.json` と `docs/journal/phase-5.md` の実測を転記する。

---

## Model summary

- **Base**: [unsloth/Qwen3-4B](https://huggingface.co/unsloth/Qwen3-4B)(Qwen3-4B、Apache-2.0)
- **Method**: QLoRA(4bit NF4 base + LoRA r=16, alpha=16, dropout 0, all linear projections)via [unsloth](https://github.com/unslothai/unsloth) + TRL SFTTrainer
- **Data**: 約 1,000 組の日本語 Q&A。[local_llm](https://github.com/Moro-1025/local_llm) リポジトリの `docs/` と README、および [ai_harness](https://github.com/Moro-1025/ai_harness) の Markdown から、Qwen3.6-35B-A3B(llama-server)で合成。合成プロンプトと条件は `training/data/qa.meta.json`
- **Language**: Japanese(技術文書)
- **Formats**: LoRA adapter(safetensors)、merged F16、GGUF Q4_K_M(llama.cpp)
- **Hardware**: RTX 5070 12 GB、WSL2 Ubuntu 24.04、学習時間と VRAM ピークは下表

## Intended use

local_llm プロジェクトと ai_harness のドキュメントに関する質問応答のデモ。Phase 6 で「RAG あり / LoRA のみ / 素のモデル」を比較するための実験用モデル。
一般的な用途には素の Qwen3-4B を使うこと。

## Training details

| 項目 | 値 |
| --- | --- |
| epochs | 2 |
| learning rate | 2e-4(linear, warmup 5 steps) |
| effective batch | 16(4 × grad. accum. 4) |
| max_seq_length | 2048 |
| optimizer | adamw_8bit, weight decay 0.01 |
| loss | assistant 応答のみ(train_on_responses_only) |
| train / eval rows | 1,324 / 147 |
| eval loss first → last | 3.906 → 1.678(166 steps) |
| wall time | 290 s(RTX 5070) |
| VRAM peak(reserved) | 5.91 GiB |
| 暫定評価(20 問、キーワード一致) | LoRA 20% / base 20%(同率。Phase 6 で再評価) |

## Prompt format

Qwen3 の chat template。学習データは thinking 無し(空の `<think></think>` を先頭に付けた assistant 応答)。
system prompt: 「あなたは local_llm プロジェクトと ai_harness のドキュメントに詳しいアシスタントです。日本語で簡潔に答えてください。」

## Limitations

- 学習データは合成 Q&A で、元文書の誤りや合成モデルの言い換えをそのまま含む可能性がある
- 文書に無い内容を聞かれたときの振る舞いは評価していない(Phase 6)
- 評価は暫定(キーワード一致)。LLM 採点は Phase 6

## Reproduce

```
training/setup-wsl.sh → data/build_dataset.py → data/split.py → train_lora.py → export_gguf.sh runs/<日時>
```

詳細は `training/README.md`。
