"""CLI の会話ログを JSONL に保存する。

1 メッセージ 1 行で `logs/chat-<日時>.jsonl` に追記する。
ファイルは最初のメッセージを書くときに作る(起動だけして終了した場合は作らない)。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class ChatLogger:
    """会話を JSONL ファイルに追記するロガー。"""

    def __init__(self, log_dir: str | Path = "logs", *, path: Path | None = None) -> None:
        self.log_dir = Path(log_dir)
        self._path = path

    @property
    def path(self) -> Path:
        """ログファイルのパス。初回アクセス時に日時から決める。"""
        if self._path is None:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            self._path = self.log_dir / f"chat-{stamp}.jsonl"
        return self._path

    def append(self, role: str, content: str, model: str, **extra: Any) -> None:
        """1 メッセージを 1 行の JSON として追記する。"""
        record: dict[str, Any] = {
            "time": _now_iso(),
            "role": role,
            "content": content,
            "model": model,
        }
        record.update(extra)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_log(path: str | Path) -> list[dict[str, Any]]:
    """JSONL ログを読み込んで辞書のリストで返す(テストや再開用)。"""
    records: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records
