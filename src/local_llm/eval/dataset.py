"""評価データセット(eval/datasets/<name>-v<N>.jsonl)の読み込み。

契約 DATASET_VERSIONED: ファイル名の版番号を結果に記録し、中身を変えるときは版を上げる。
全件が重いときは seed 固定の部分集合を使い、使った件数・seed・行番号を結果に残す。
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import dataclass
from pathlib import Path

_VERSION_RE = re.compile(r"^(?P<name>.+)-v(?P<version>\d+)\.jsonl$")


@dataclass(frozen=True)
class QaItem:
    """1 問。index は元ファイルでの行番号(0 始まり)で、部分集合でもどの問いかを追える。"""

    index: int
    question: str
    answer: str
    source: str

    @property
    def source_path(self) -> str:
        """`相対パス#見出し` のパス部分(検索ヒットの判定に使う)。"""
        return self.source.split("#", 1)[0].replace("\\", "/")


@dataclass(frozen=True)
class Dataset:
    path: Path
    name: str
    version: int
    sha256: str
    items: list[QaItem]

    @property
    def total(self) -> int:
        return len(self.items)


def parse_dataset_name(path: Path) -> tuple[str, int]:
    m = _VERSION_RE.match(path.name)
    if not m:
        raise ValueError(f"データセット名は <name>-v<N>.jsonl の形にしてください: {path.name}")
    return m.group("name"), int(m.group("version"))


def load_dataset(path: Path | str) -> Dataset:
    path = Path(path)
    name, version = parse_dataset_name(path)
    raw = path.read_bytes()
    items: list[QaItem] = []
    for i, line in enumerate(raw.decode("utf-8").splitlines()):
        if not line.strip():
            continue
        row = json.loads(line)
        for key in ("question", "answer", "source"):
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise ValueError(f"{path.name} の {i + 1} 行目に {key} がありません")
        items.append(QaItem(index=i, question=row["question"], answer=row["answer"], source=row["source"]))
    if not items:
        raise ValueError(f"{path.name} に問題がありません")
    return Dataset(path=path, name=name, version=version, sha256=hashlib.sha256(raw).hexdigest(), items=items)


def select_items(dataset: Dataset, n: int | None, seed: int) -> list[QaItem]:
    """n 件を seed 固定で選ぶ(n が None か全件以上なら全件、元の順序を保つ)。"""
    if n is None or n >= dataset.total:
        return list(dataset.items)
    if n <= 0:
        raise ValueError("--n は 1 以上にしてください")
    rng = random.Random(seed)
    chosen = sorted(rng.sample(range(dataset.total), n))
    return [dataset.items[i] for i in chosen]
