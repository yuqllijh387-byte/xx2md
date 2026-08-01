"""Tests for convert_research_doc helpers.

Covers:
- ensure_localhost_no_proxy keeps loopback off proxies (system-proxy fix).
- single-shot MinerU runs produce the merged content list expected by
  select_semantic_pages.py (regression: small PDFs never hit the batched
  path and the merged file was missing).
"""

import json

import convert_research_doc as m


def test_ensure_localhost_no_proxy_sets_both_cases(monkeypatch):
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    m.ensure_localhost_no_proxy()
    for var in ("no_proxy", "NO_PROXY"):
        hosts = m.os.environ[var].split(",")
        for host in ("127.0.0.1", "localhost", "::1"):
            assert host in hosts


def test_ensure_localhost_no_proxy_preserves_existing(monkeypatch):
    monkeypatch.setenv("no_proxy", "example.com,127.0.0.1")
    monkeypatch.setenv("NO_PROXY", "")
    m.ensure_localhost_no_proxy()
    hosts = m.os.environ["no_proxy"].split(",")
    assert "example.com" in hosts
    assert hosts.count("127.0.0.1") == 1
    assert "localhost" in hosts


def _write_content_list(path, page_indices):
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"page_idx": i, "text": f"page {i}"} for i in page_indices]
    path.write_text(json.dumps(rows), encoding="utf-8")


def test_merge_mineru_batch_content_lists_single_shot(tmp_path):
    """The record shape used by the single-shot path in run_mineru."""
    _write_content_list(tmp_path / "doc" / "auto" / "doc_content_list.json", [0, 1, 2])
    record = {
        "start_page": 0,
        "end_page": 2,
        "status": "completed",
        "content_list": "doc/auto/doc_content_list.json",
    }
    out = m.merge_mineru_batch_content_lists(tmp_path, [record])
    merged = json.loads(out.read_text(encoding="utf-8"))
    assert out.name == "__research_doc_ingest_merged_content_list.json"
    assert [row["page_idx"] for row in merged] == [0, 1, 2]


def test_merge_mineru_batch_content_lists_offsets_relative_indices(tmp_path):
    _write_content_list(tmp_path / "b1" / "a_content_list.json", [0, 1])
    _write_content_list(tmp_path / "b2" / "b_content_list.json", [0, 1])
    records = [
        {"start_page": 0, "end_page": 1, "status": "completed", "content_list": "b1/a_content_list.json"},
        {"start_page": 2, "end_page": 3, "status": "completed", "content_list": "b2/b_content_list.json"},
    ]
    out = m.merge_mineru_batch_content_lists(tmp_path, records)
    merged = json.loads(out.read_text(encoding="utf-8"))
    assert [row["page_idx"] for row in merged] == [0, 1, 2, 3]


def test_merge_mineru_batch_content_lists_skips_incomplete(tmp_path):
    _write_content_list(tmp_path / "b1" / "a_content_list.json", [0])
    records = [
        {"start_page": 0, "end_page": 0, "status": "error", "content_list": "b1/a_content_list.json"},
    ]
    out = m.merge_mineru_batch_content_lists(tmp_path, records)
    assert json.loads(out.read_text(encoding="utf-8")) == []


def test_select_mineru_content_list_excludes_v2(tmp_path):
    _write_content_list(tmp_path / "d" / "doc_content_list.json", [0])
    _write_content_list(tmp_path / "d" / "doc_content_list_v2.json", [0, 1, 2, 3])
    found = m.select_mineru_content_list(tmp_path)
    assert found is not None
    assert found.name == "doc_content_list.json"


def test_select_mineru_content_list_missing(tmp_path):
    assert m.select_mineru_content_list(tmp_path) is None
