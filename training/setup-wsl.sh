#!/usr/bin/env bash
# Phase 5: WSL Ubuntu に LoRA 学習環境を作る。
#
#   wsl -d Ubuntu -- bash -lc "cd /mnt/c/dev/local_llm/training && ./setup-wsl.sh"
#
# やること:
#   1. uv(Python パッケージ管理)を ~/.local/bin に入れる(未導入なら)
#   2. uv 管理の Python 3.12 で venv を作る(場所は $TRAINING_VENV、既定 ~/venvs/local_llm-training。
#      /mnt/c 上に置くと遅いので WSL のディスク側に置く)
#   3. requirements.in を入れ、固定版を requirements.lock.txt に書き出す
#   4. nvidia-smi と torch.cuda.is_available() を表示する(LORA-001 の証拠)
#
# 前提: gcc が必要(unsloth の Triton カーネルが実行時にコンパイルする)。無ければ root で入れる:
#   wsl -d Ubuntu -u root -- apt-get install -y build-essential
set -euo pipefail
cd "$(dirname "$0")"

VENV="${TRAINING_VENV:-$HOME/venvs/local_llm-training}"
export PATH="$HOME/.local/bin:$PATH"

echo "== 1. uv"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
uv --version

echo "== 2. venv ($VENV)"
if ! command -v gcc >/dev/null 2>&1; then
  echo "gcc が見つかりません。先に  wsl -d Ubuntu -u root -- apt-get install -y build-essential  を実行してください" >&2
  exit 1
fi
mkdir -p "$(dirname "$VENV")"
uv python install 3.12
[ -d "$VENV" ] || uv venv --python 3.12 "$VENV"
# shellcheck disable=SC1091
source "$VENV/bin/activate"

echo "== 3. 依存の導入"
if [ -f requirements.lock.txt ] && [ -z "${REFRESH_LOCK:-}" ]; then
  uv pip install -r requirements.lock.txt
else
  uv pip install -r requirements.in
  uv pip freeze > requirements.lock.txt
  echo "requirements.lock.txt を更新しました"
fi

echo "== 4. GPU 確認"
nvidia-smi --query-gpu=name,memory.total,memory.used,driver_version --format=csv
python - <<'PY'
import torch
print("torch", torch.__version__, "cuda", torch.version.cuda)
print("torch.cuda.is_available():", torch.cuda.is_available())
if torch.cuda.is_available():
    p = torch.cuda.get_device_properties(0)
    print(f"device: {p.name}  VRAM: {p.total_memory/2**30:.2f} GiB  capability: sm_{p.major}{p.minor}")
PY
echo "== 完了。以後は  source $VENV/bin/activate  で有効化"
