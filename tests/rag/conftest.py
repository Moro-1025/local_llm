"""RAG テスト共通: `/v1/embeddings` を決定的な疑似ベクトルでモックする。

本物の埋め込みモデルは使えない(GPU なし)ので、テキストに含まれるキーワードで
one-hot 風のベクトルを作る。同じ語を含むクエリとチャンクが近くなる。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import httpx
import numpy as np
import pytest

# tests/ の conftest(BASE_URL など)を import できるようにする
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DIM = 32


def fake_vector(text: str) -> list[float]:
    """単語(空白・句読点区切り)ごとに hash → 1 次元を立てる。"""
    vec = np.zeros(DIM, dtype=np.float64)
    tokens = [t for t in text.replace("\n", " ").replace("、", " ").replace("。", " ").split(" ") if t]
    for tok in tokens:
        h = int(hashlib.md5(tok.lower().encode("utf-8")).hexdigest(), 16)
        vec[h % DIM] += 1.0
    if not tokens:
        vec[0] = 1.0
    return vec.tolist()


def embeddings_side_effect(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    inputs = body["input"]
    if isinstance(inputs, str):
        inputs = [inputs]
    data = [{"object": "embedding", "index": i, "embedding": fake_vector(t)} for i, t in enumerate(inputs)]
    return httpx.Response(
        200, json={"object": "list", "data": data, "model": body.get("model"), "usage": {"prompt_tokens": 0}}
    )


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    """小さな Markdown コーパスを tmp に作る。"""
    root = tmp_path / "corpus"
    (root / "guide").mkdir(parents=True)
    (root / "guide" / "budget.md").write_text(
        "---\ntitle: \"実行予算ガイド\"\n---\n# 実行予算\n\n"
        "## max_steps\n\nmax_steps はエージェントの最大ステップ数。超えたら budget_exceeded で停止する。\n\n"
        "## same_error_limit\n\n同じエラーが続いた回数の上限。既定は 2。\n",
        encoding="utf-8",
    )
    (root / "evidence.md").write_text(
        "# 証拠\n\n自己申告より証拠を優先する。テスト結果 ログ 差分 を対象バージョンに結び付ける。\n",
        encoding="utf-8",
    )
    (root / "notes.txt").write_text("これは Markdown ではないので読まれない", encoding="utf-8")
    return root
