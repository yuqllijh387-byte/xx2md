"""Tests for pure helpers in describe_pages."""

import pytest

import describe_pages as m


def test_parse_pages_single_and_range():
    assert m.parse_pages("4,6,8") == [4, 6, 8]
    assert m.parse_pages("4-6") == [4, 5, 6]
    assert m.parse_pages("2,4-5") == [2, 4, 5]


def test_parse_pages_rejects_empty_and_reversed():
    with pytest.raises(ValueError):
        m.parse_pages("")
    with pytest.raises(ValueError):
        m.parse_pages("6-4")


def test_compact_text_short_text_unchanged():
    assert m.compact_text("hello", 100) == "hello"


def test_compact_text_truncates_with_marker():
    text = "a" * 500 + "b" * 500
    out = m.compact_text(text, 200)
    assert "[...middle omitted...]" in out
    assert len(out) < len(text)


def test_clean_model_text_strips_fences():
    assert m.clean_model_text("```\nbody\n```") == "body"
    assert m.clean_model_text("body") == "body"
