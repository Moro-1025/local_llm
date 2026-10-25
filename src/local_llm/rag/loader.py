"""コーパス(Markdown ファイル)の読み込み。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

_FRONT_MATTER = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n", re.DOTALL)
_H1 = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Document:
    """読み込んだ 1 文書。path はコーパスのルートからの相対パス(区切りは `/`)。"""

    path: str
    title: str
    text: str
    sha256: str


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def extract_title(text: str, fallback: str) -> str:
    """YAML front matter の title か、最初の `# 見出し` をタイトルにする。"""
    fm = _FRONT_MATTER.match(text)
    if fm:
        m = re.search(r"^title:\s*[\"']?(.+?)[\"']?\s*$", fm.group(0), re.MULTILINE)
        if m:
            return m.group(1).strip()
    h1 = _H1.search(text)
    if h1:
        return h1.group(1).strip()
    return fallback


def strip_front_matter(text: str) -> str:
    return _FRONT_MATTER.sub("", text, count=1)


def load_document(root: Path, file: Path) -> Document:
    raw = file.read_text(encoding="utf-8-sig")
    rel = file.relative_to(root).as_posix()
    return Document(
        path=rel,
        title=extract_title(raw, fallback=file.stem),
        text=strip_front_matter(raw),
        sha256=sha256_text(raw),
    )


def load_documents(root: Path, pattern: str = "**/*.md") -> list[Document]:
    """root 配下の Markdown をパス順に読む。root が無ければ空リスト。"""
    if not root.is_dir():
        return []
    files = sorted(p for p in root.glob(pattern) if p.is_file())
    return [load_document(root, p) for p in files]
