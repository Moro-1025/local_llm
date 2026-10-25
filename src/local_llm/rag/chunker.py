"""Markdown を検索単位(チャンク)に分割する。

方針:
1. 見出し(`#`〜`######`)で節に分ける。節の見出し階層は "親 > 子" の形で持つ
2. 節が chunk_size 文字を超えるときは、段落(空行)の境界を優先して切り、
   直前のチャンクの末尾 overlap 文字を先頭に重ねる(文脈の断絶を和らげる)
3. 短すぎる節(見出しだけなど)は次の節に繰り込む

文字数ベースなのはトークナイザに依存しないため。日本語は 1 文字 ≒ 1 トークン前後で、
800 文字なら埋め込みモデルのコンテキスト(数千トークン)に十分収まる。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")

DEFAULT_CHUNK_SIZE = 800
DEFAULT_OVERLAP = 100
_MIN_SECTION_CHARS = 40


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    heading: str
    text: str


@dataclass
class _Section:
    heading: str
    lines: list[str]

    @property
    def text(self) -> str:
        return "\n".join(self.lines).strip()


def split_sections(text: str) -> list[_Section]:
    """見出しで節に分ける。コードフェンス内の `#` は見出し扱いしない。"""
    sections: list[_Section] = [_Section(heading="", lines=[])]
    stack: list[tuple[int, str]] = []  # (level, title)
    in_fence = False
    for line in text.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        m = None if in_fence else _HEADING.match(line)
        if m:
            level, title = len(m.group(1)), m.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            sections.append(_Section(heading=" > ".join(t for _, t in stack), lines=[]))
            continue
        sections[-1].lines.append(line)
    # 見出しだけ・短すぎる節は次の節へ繰り込む
    merged: list[_Section] = []
    for sec in sections:
        if merged and len(merged[-1].text) < _MIN_SECTION_CHARS:
            prev = merged.pop()
            head = prev.heading if prev.heading and not sec.heading else sec.heading
            lines = ([prev.heading] if prev.heading and prev.heading != head else []) + prev.lines + sec.lines
            sec = _Section(heading=head, lines=lines)
        merged.append(sec)
    return [s for s in merged if s.text]


def _split_long(text: str, chunk_size: int, overlap: int) -> list[str]:
    """段落境界を優先しつつ chunk_size 以下に切り、overlap 文字を重ねる。"""
    if len(text) <= chunk_size:
        return [text]
    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            # 範囲の後半にある段落境界・改行で切る
            cut = text.rfind("\n\n", start + chunk_size // 2, end)
            if cut == -1:
                cut = text.rfind("\n", start + chunk_size // 2, end)
            if cut != -1:
                end = cut
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return pieces


def chunk_text(
    text: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[Chunk]:
    """文書全体をチャンク列にする。"""
    if overlap >= chunk_size:
        raise ValueError("overlap は chunk_size より小さくしてください")
    chunks: list[Chunk] = []
    for sec in split_sections(text):
        for piece in _split_long(sec.text, chunk_size, overlap):
            chunks.append(Chunk(ordinal=len(chunks), heading=sec.heading, text=piece))
    return chunks
