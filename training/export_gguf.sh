#!/usr/bin/env bash
# LoRA アダプタをベースモデルにマージし、GGUF(F16 → Q4_K_M)に変換して models/ に置く(Phase 5 / LORA-004)。
#
#   wsl -d Ubuntu -- bash -lc "cd /mnt/c/dev/local_llm/training && ./export_gguf.sh runs/<日時>"
#
# 手順:
#   1. unsloth でアダプタを読み、16bit にマージして export/merged/ に保存(約 8 GB、git 管理外)
#   2. llama.cpp の convert_hf_to_gguf.py で F16 GGUF に変換(~/llama.cpp を shallow clone。gguf-py はそこから使う)
#   3. Windows 側の llama-local/llama-quantize.exe で Q4_K_M に量子化(WSL の interop で .exe を直接呼ぶ)
#   4. 出来た GGUF を $MODELS_DIR(既定: C:\dev\local_llm\models)に置く。models.ini の [local/...] がこの絶対パスを指す
set -euo pipefail
cd "$(dirname "$0")"
RUN_DIR="${1:?使い方: ./export_gguf.sh runs/<日時>}"
NAME="${2:-qwen3-4b-localllm-lora}"
VENV="${TRAINING_VENV:-$HOME/venvs/local_llm-training}"
LLAMA_CPP="${LLAMA_CPP_DIR:-$HOME/llama.cpp}"
MODELS_DIR_WIN="${MODELS_DIR:-C:\\dev\\local_llm\\models}"
QUANTIZE_EXE="${QUANTIZE_EXE:-/mnt/c/dev/local_llm/llama-local/llama-quantize.exe}"

# shellcheck disable=SC1091
source "$VENV/bin/activate"
[ -d "$RUN_DIR/adapter" ] || { echo "アダプタが無い: $RUN_DIR/adapter" >&2; exit 1; }
[ -d "$LLAMA_CPP" ] || git clone --depth 1 https://github.com/ggml-org/llama.cpp "$LLAMA_CPP"
[ -x "$QUANTIZE_EXE" ] || { echo "llama-quantize.exe が無い: $QUANTIZE_EXE" >&2; exit 1; }

MERGED="export/merged"
F16="export/$NAME-F16.gguf"
Q4="export/$NAME-Q4_K_M.gguf"

echo "== 1. マージ(16bit) -> $MERGED"
python - "$RUN_DIR/adapter" "$MERGED" <<'PY'
import sys
from unsloth import FastLanguageModel
adapter, out = sys.argv[1], sys.argv[2]
model, tokenizer = FastLanguageModel.from_pretrained(adapter, max_seq_length=2048, load_in_4bit=True, dtype=None)
model.save_pretrained_merged(out, tokenizer, save_method="merged_16bit")
print("merged ->", out)
PY

echo "== 2. GGUF F16 -> $F16"
PYTHONPATH="$LLAMA_CPP/gguf-py" python "$LLAMA_CPP/convert_hf_to_gguf.py" "$MERGED" --outfile "$F16" --outtype f16

echo "== 3. 量子化 Q4_K_M -> $Q4(Windows の llama-quantize.exe)"
"$QUANTIZE_EXE" "$(wslpath -w "$F16")" "$(wslpath -w "$Q4")" Q4_K_M

echo "== 4. models/ に配置"
MODELS_DIR_WSL="$(wslpath -u "$MODELS_DIR_WIN")"
mkdir -p "$MODELS_DIR_WSL"
mv -f "$Q4" "$MODELS_DIR_WSL/$NAME-Q4_K_M.gguf"
rm -f "$F16"                      # 8 GB の中間ファイルは消す(再生成できる)
ls -la "$MODELS_DIR_WSL/$NAME-Q4_K_M.gguf"
echo "models.ini の [local/$NAME] に  model = $MODELS_DIR_WIN\\$NAME-Q4_K_M.gguf  を書く"
