#!/usr/bin/env bash
# ai_harness(Moro-1025/ai_harness)の Markdown を data/corpus/ai_harness/ に取得する。
# git には入れない(ルートの .gitignore で data/ が除外されている)。
# 固定 commit から raw.githubusercontent.com で取得するので、再実行しても同じ内容になる。
#
#   bash training/data/fetch_corpus.sh            # リポジトリのルートで
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DEST="$ROOT/data/corpus/ai_harness"
REPO="Moro-1025/ai_harness"
COMMIT="${AI_HARNESS_COMMIT:-d402da48c60db6faa21ed9e69413acf4116eafe6}"   # 2026-10-25 時点の main
FILES=(
  AI_Agent_Development_Handbook_JA.md
  AI_Coding_Agent_Usage_Harness_Handbook_JA.md
  README.md
  docs/README.md
  docs/core/decision-model.md
  docs/core/glossary.md
  docs/core/principles.md
  docs/learning/beginner/coding-agent-tutorial.md
  docs/learning/beginner/harness-basics.md
  docs/learning/beginner/simple-agent-tutorial.md
  docs/learning/intermediate/agent-runtime.md
  docs/learning/intermediate/ci-review-and-evaluation.md
  docs/learning/intermediate/evidence-and-policy.md
  docs/learning/intermediate/team-harness.md
  docs/profiles/codex.md
  docs/profiles/distributed-execution.md
  docs/profiles/personal-local.md
  docs/profiles/production-high-risk.md
  docs/profiles/team-ci.md
  docs/reference/agent-development-handbook.md
  docs/reference/coding-agent-usage-handbook.md
)
mkdir -p "$DEST"
for f in "${FILES[@]}"; do
  out="$DEST/$f"
  mkdir -p "$(dirname "$out")"
  if [ -s "$out" ]; then echo "skip  $f"; continue; fi
  echo "fetch $f"
  curl -fsSL --retry 3 "https://raw.githubusercontent.com/$REPO/$COMMIT/$f" -o "$out"
done
echo "$COMMIT" > "$DEST/.commit"
echo "done: $(find "$DEST" -name '*.md' | wc -l) files -> $DEST"
