"""設定の読み込み。

環境変数と `.env`(python-dotenv)から読む。優先順位は
「既に設定されている環境変数 > .env > 既定値」。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_BASE_URL = "http://127.0.0.1:8080"
# models.ini では UD-Q4_K_M だが、llama-server のルーターモードは id から "UD-" を落として公開する
# (Hugging Face 上では両タグが同じファイルに解決される)。API に渡す id はこちら
DEFAULT_MODEL = "unsloth/Qwen3.6-35B-A3B-GGUF:Q4_K_M"
DEFAULT_DB_PATH = "data/chat.db"
DEFAULT_TIMEOUT = 600.0


@dataclass(frozen=True)
class Settings:
    """アプリ全体の設定値。"""

    base_url: str = DEFAULT_BASE_URL
    default_model: str = DEFAULT_MODEL
    db_path: Path = Path(DEFAULT_DB_PATH)
    timeout: float = DEFAULT_TIMEOUT


def load_settings(env_file: str | os.PathLike[str] | None = None) -> Settings:
    """環境変数と .env を読んで Settings を返す。

    Args:
        env_file: 読み込む .env のパス。None ならカレントディレクトリから探す。
    """
    # override=False なので、既に設定済みの環境変数は .env で上書きされない
    load_dotenv(env_file, override=False)

    return Settings(
        base_url=os.environ.get("LLAMA_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        default_model=os.environ.get("LLM_DEFAULT_MODEL", DEFAULT_MODEL),
        db_path=Path(os.environ.get("CHAT_DB_PATH", DEFAULT_DB_PATH)),
        timeout=float(os.environ.get("LLAMA_TIMEOUT", DEFAULT_TIMEOUT)),
    )
