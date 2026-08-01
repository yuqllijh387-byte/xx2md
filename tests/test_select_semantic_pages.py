"""Tests for pure helpers in select_semantic_pages."""

import select_semantic_pages as m


def test_plain_text_strips_html():
    assert m.plain_text("<b>hello</b> &amp; bye") == "hello & bye"


def test_plain_text_collapses_whitespace():
    assert m.plain_text("a\n\n  b\tc") == "a b c"
