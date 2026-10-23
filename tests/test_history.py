"""ChatLogger の JSONL 保存のテスト。"""

from __future__ import annotations

from pathlib import Path

from local_llm.history import ChatLogger, read_log


def test_logger_appends_one_json_per_line(tmp_path: Path):
    logger = ChatLogger(tmp_path)
    logger.append("user", "こんにちは", "model-a")
    logger.append("assistant", "やあ", "model-a", reasoning="考えた")

    assert logger.path.parent == tmp_path
    assert logger.path.name.startswith("chat-") and logger.path.suffix == ".jsonl"

    records = read_log(logger.path)
    assert [r["role"] for r in records] == ["user", "assistant"]
    assert records[0]["content"] == "こんにちは"
    assert records[0]["model"] == "model-a"
    assert "time" in records[0]
    assert records[1]["reasoning"] == "考えた"


def test_logger_does_not_create_file_until_first_append(tmp_path: Path):
    logger = ChatLogger(tmp_path / "logs")
    assert not (tmp_path / "logs").exists()
    logger.append("user", "x", "m")
    assert logger.path.exists()
