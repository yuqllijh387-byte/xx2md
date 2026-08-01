"""Regression tests for build_routed_markdown.

Covers the NameError crash where clean_semantic_markdown called the
undefined is_branding_or_confidentiality helper.
"""

import build_routed_markdown as m


def test_is_branding_or_confidentiality_defined():
    assert callable(m.is_branding_or_confidentiality)


def test_is_branding_or_confidentiality_matches_labels():
    assert m.is_branding_or_confidentiality("内部使用") is True
    assert m.is_branding_or_confidentiality("严格保密") is True
    assert m.is_branding_or_confidentiality("Confidential") is True
    assert m.is_branding_or_confidentiality("正文内容 body text") is False


def test_clean_semantic_markdown_does_not_raise():
    out = m.clean_semantic_markdown("### 视觉与版面语义\n内部使用\n正文保留。", "augment")
    assert "内部使用" not in out
    assert "正文保留" in out


def test_clean_semantic_markdown_strips_images_and_fences():
    out = m.clean_semantic_markdown("```\n![img](assets/x.png)\nkept text\n```", "augment")
    assert "![" not in out
    assert "kept text" in out


def test_clean_semantic_markdown_heading_levels():
    out = m.clean_semantic_markdown("# T\nbody", "augment")
    assert out.splitlines()[0] == "### T"
    out = m.clean_semantic_markdown("# T\nbody", "rewrite")
    assert out.splitlines()[0] == "### T"


def test_clean_semantic_markdown_noop_answer_returns_empty():
    assert m.clean_semantic_markdown("无需补充。", "augment") == ""


def test_clean_mineru_page_regression():
    out = m.clean_mineru_page("内部使用\n*header: x*\n*page_number: 3*\nnormal line")
    assert "normal line" in out
    assert "内部使用" not in out
    assert "*header:" not in out
    assert "*page_number:" not in out
