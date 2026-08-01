"""Tests for build_chunks page splitting."""

import build_chunks as m


def test_page_chunks_english_headings():
    content = "## Page 1\nalpha\n## Page 2\nbeta\n"
    chunks = m.page_chunks(content)
    assert [page for page, _ in chunks] == [1, 2]
    assert "alpha" in chunks[0][1]
    assert "beta" in chunks[1][1]


def test_page_chunks_chinese_headings():
    """Routed final Markdown uses ## 第 N 页 headings."""
    content = "# Title\n\n## 第 1 页\n甲\n## 第 2 页\n乙\n## 第 3 页\n丙\n"
    chunks = m.page_chunks(content)
    assert [page for page, _ in chunks] == [None, 1, 2, 3]
    assert "甲" in chunks[1][1]
    assert "丙" in chunks[3][1]


def test_page_chunks_no_headings_single_chunk():
    chunks = m.page_chunks("plain text without headings")
    assert chunks == [(None, "plain text without headings")]
