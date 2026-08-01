"""Tests for clean_package minimal-package reduction."""

import json

import pytest

import clean_package as m


def make_package(tmp_path, final_md=True):
    pkg = tmp_path / "pkg"
    (pkg / "assets" / "img").mkdir(parents=True)
    (pkg / "engine_output" / "mineru").mkdir(parents=True)
    (pkg / ".engine_work").mkdir(parents=True)
    (pkg / "content.md").write_text("## Page 1\nbaseline text\n", encoding="utf-8")
    (pkg / "structure.json").write_text(
        json.dumps({"source": "/tmp/source.pdf"}), encoding="utf-8"
    )
    (pkg / "audit.md").write_text("# audit\n", encoding="utf-8")
    (pkg / "baseline.json").write_text("{}", encoding="utf-8")
    (pkg / "semantic_selection.json").write_text("{}", encoding="utf-8")
    (pkg / "chunks.jsonl").write_text('{"old": true}\n', encoding="utf-8")
    (pkg / "assets" / "img" / "a.jpg").write_bytes(b"x")
    (pkg / "engine_output" / "mineru" / "raw.md").write_text("raw", encoding="utf-8")
    if final_md:
        (pkg / "doc.final.md").write_text(
            "## Page 1\nfinal one\n## Page 2\nfinal two\n", encoding="utf-8"
        )
    return pkg


def test_clean_package_keeps_final_md_and_rebuilt_chunks(tmp_path):
    pkg = make_package(tmp_path)
    result = m.clean_package(pkg)
    remaining = sorted(item.name for item in pkg.iterdir())
    assert remaining == ["chunks.jsonl", "doc.final.md"]
    rows = [json.loads(line) for line in (pkg / "chunks.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [row["page_or_slide"] for row in rows] == ["page:1", "page:2"]
    assert all(row["derived_from"] == "doc.final.md" for row in rows)
    assert all(row["source"] == "/tmp/source.pdf" for row in rows)
    assert result["kept_markdown"] == "doc.final.md"
    assert result["chunk_count"] == 2


def test_clean_package_falls_back_to_content_md(tmp_path):
    pkg = make_package(tmp_path, final_md=False)
    result = m.clean_package(pkg)
    remaining = sorted(item.name for item in pkg.iterdir())
    assert remaining == ["chunks.jsonl", "content.md"]
    assert result["kept_markdown"] == "content.md"


def test_clean_package_dry_run_deletes_nothing(tmp_path):
    pkg = make_package(tmp_path)
    result = m.clean_package(pkg, dry_run=True)
    assert result["dry_run"] is True
    assert (pkg / "structure.json").exists()
    assert (pkg / "assets").is_dir()
    assert "engine_output" in result["removed"]


def test_clean_package_explicit_keep(tmp_path):
    pkg = make_package(tmp_path)
    result = m.clean_package(pkg, keep="content.md")
    remaining = sorted(item.name for item in pkg.iterdir())
    assert remaining == ["chunks.jsonl", "content.md"]
    assert result["kept_markdown"] == "content.md"


def test_clean_package_missing_keep_raises(tmp_path):
    pkg = make_package(tmp_path)
    with pytest.raises(SystemExit):
        m.clean_package(pkg, keep="nope.md")
