#!/usr/bin/env bash
# training/ のテスト(WSL で実行)。scripts/agent/verify.ps1 -Training から呼ばれる。
#   wsl -d Ubuntu -- bash -lc "cd /mnt/c/dev/local_llm/training && ./run_tests.sh"
set -euo pipefail
cd "$(dirname "$0")"
VENV="${TRAINING_VENV:-$HOME/venvs/local_llm-training}"
if [ ! -x "$VENV/bin/python" ]; then
  echo "venv が無い: $VENV  (先に ./setup-wsl.sh を実行)" >&2
  exit 1
fi
exec "$VENV/bin/python" -m pytest -q -p no:cacheprovider tests "$@"
