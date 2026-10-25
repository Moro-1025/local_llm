"""qa.jsonl を train/eval に 9:1 で分け、eval 側を eval/datasets/qa-v1.jsonl にもコピーする(評価汚染の防止)。

    uv run python training/data/split.py            # 既定: seed 42、eval 比率 0.1
    uv run python training/data/split.py --version 2

同じ seed なら同じ分割になる(決定論的)。eval の質問は学習に使わず、Phase 6 の評価にも同じファイルを使う。
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(__file__).resolve().parent


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def split(rows: list[dict], *, eval_ratio: float, seed: int) -> tuple[list[dict], list[dict]]:
    idx = list(range(len(rows)))
    random.Random(seed).shuffle(idx)
    n_eval = max(1, round(len(rows) * eval_ratio))
    eval_idx = set(idx[:n_eval])
    train = [r for i, r in enumerate(rows) if i not in eval_idx]
    evals = [r for i, r in enumerate(rows) if i in eval_idx]
    return train, evals


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src", default=str(DATA_DIR / "qa.jsonl"))
    ap.add_argument("--eval-ratio", type=float, default=0.1)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--version", type=int, default=1, help="eval/datasets/qa-v<N>.jsonl の N")
    args = ap.parse_args(argv)

    rows = read_jsonl(Path(args.src))
    train, evals = split(rows, eval_ratio=args.eval_ratio, seed=args.seed)
    write_jsonl(DATA_DIR / "train.jsonl", train)
    write_jsonl(DATA_DIR / "eval.jsonl", evals)
    dest = ROOT / "eval" / "datasets" / f"qa-v{args.version}.jsonl"
    write_jsonl(dest, evals)
    print(f"total {len(rows)} -> train {len(train)} / eval {len(evals)}  (seed {args.seed})")
    print(f"eval copy -> {dest.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
