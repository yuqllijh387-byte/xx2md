"""Tests for check_environment pure helpers."""

import re

import check_environment as m


def test_distribution_status_found():
    status = m.distribution_status("pip", "0.0.0")
    assert status["installed"] is not None
    assert status["ok"] is False  # version never matches the fake pin


def test_distribution_status_missing():
    status = m.distribution_status("definitely-not-a-real-dist-xyz", "1.0.0")
    assert status["installed"] is None
    assert status["ok"] is False


def test_required_distributions_match_requirements_txt():
    """The pinned stack in requirements.txt must stay in sync with the checker."""
    requirements = (
        __import__("pathlib").Path(__file__).resolve().parent.parent
        / "research-doc-ingest" / "requirements.txt"
    ).read_text(encoding="utf-8")
    normalized = re.sub(r"\[[^\]]*\]", "", requirements)  # strip extras like [pipeline]
    for name, expected in m.REQUIRED_DISTRIBUTIONS.items():
        assert f"{name}=={expected}" in normalized, (
            f"requirements.txt lost the pin {name}=={expected}"
        )
