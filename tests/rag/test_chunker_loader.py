"""loader / chunker の単体テスト(RAG-004)。"""

from __future__ import annotations

from pathlib import Path

import pytest

from local_llm.rag.chunker import chunk_text, split_sections
from local_llm.rag.loader import extract_title, load_documents


def test_load_documents_reads_only_markdown(corpus: Path):
    docs = load_documents(corpus)
    assert [d.path for d in docs] == ["evidence.md", "guide/budget.md"]
    budget = docs[1]
    assert budget.title == "実行予算ガイド"  # front matter の title
    assert not budget.text.startswith("---")  # front matter は本文から外す
    assert len(budget.sha256) == 64
    assert docs[0].title == "証拠"  # H1 から


def test_load_documents_missing_dir(tmp_path: Path):
    assert load_documents(tmp_path / "nope") == []


def test_extract_title_fallback():
    assert extract_title("本文だけ", fallback="file") == "file"


def test_split_sections_tracks_heading_path_and_ignores_fences():
    text = "# A\n\nintro text that is long enough to stay as its own section here.\n\n## B\n\n```\n# not a heading\n```\nbody of B which is also long enough to be kept.\n\n### C\n\nbody of C long enough to be kept as a section too.\n\n## D\n\nbody of D long enough to be kept as a section too.\n"
    secs = split_sections(text)
    headings = [s.heading for s in secs]
    assert headings == ["A", "A > B", "A > B > C", "A > D"]
    assert "# not a heading" in secs[1].text


def test_chunk_text_respects_size_and_overlap():
    para = "あ" * 300 + "\n\n"
    text = "# 長い節\n\n" + para * 10  # 約 3000 文字
    chunks = chunk_text(text, chunk_size=800, overlap=100)
    assert len(chunks) >= 4
    assert all(len(c.text) <= 800 for c in chunks)
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    assert all(c.heading == "長い節" for c in chunks)
    # 隣り合うチャンクは重なりを持つ(前チャンク末尾の文字列が次チャンクの先頭付近にある)
    tail = chunks[0].text[-50:]
    assert tail in chunks[1].text


def test_chunk_text_merges_short_sections():
    text = "# T\n\n## 見出しだけ\n\n## 本文あり\n\nここに十分な長さの本文が入っている。もう少し足しておく。"
    chunks = chunk_text(text)
    assert len(chunks) == 1
    assert "見出しだけ" in chunks[0].text or chunks[0].heading.endswith("本文あり")


def test_chunk_text_rejects_bad_overlap():
    with pytest.raises(ValueError):
        chunk_text("x", chunk_size=100, overlap=100)
