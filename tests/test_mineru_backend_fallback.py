"""Tests for the MinerU backend auto-fallback in run_mineru.

When --mineru-backend auto resolves to a VLM backend (e.g. hybrid-engine)
and that run fails, run_mineru must retry once with the pipeline backend.
"""

import argparse
from pathlib import Path

import convert_research_doc as m


def _args(**overrides):
    base = dict(
        ocr="auto",
        mineru_backend="auto",
        mineru_url=None,
        mineru_batch_size=0,
        mineru_timeout_seconds=0,
        mineru_stall_timeout_seconds=0,
        mineru_heartbeat_seconds=0,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def _patch_common(monkeypatch, calls, results):
    monkeypatch.setattr(m, "command_path", lambda name: "/fake/mineru" if name == "mineru" else None)
    monkeypatch.setattr(m, "resolve_mineru_backend", lambda requested: "hybrid-engine")

    def fake_run(command, **kwargs):
        backend = command[command.index("-b") + 1]
        calls.append(backend)
        code = results[len(calls) - 1]
        return code, "", ""

    monkeypatch.setattr(m, "run_command_streaming", fake_run)


def test_auto_backend_falls_back_to_pipeline(monkeypatch, tmp_path):
    calls = []
    _patch_common(monkeypatch, calls, results=[1, 0])
    result = m.run_mineru(Path("doc.docx"), tmp_path / "raw", _args())
    assert calls == ["hybrid-engine", "pipeline"]
    assert result.returncode == 0
    assert any("fell back to pipeline" in w for w in result.warnings)


def test_auto_backend_no_fallback_on_success(monkeypatch, tmp_path):
    calls = []
    _patch_common(monkeypatch, calls, results=[0])
    result = m.run_mineru(Path("doc.docx"), tmp_path / "raw", _args())
    assert calls == ["hybrid-engine"]
    assert result.returncode == 0


def test_explicit_backend_never_falls_back(monkeypatch, tmp_path):
    calls = []
    _patch_common(monkeypatch, calls, results=[1])
    result = m.run_mineru(
        Path("doc.docx"), tmp_path / "raw", _args(mineru_backend="hybrid-engine")
    )
    assert calls == ["hybrid-engine"]
    assert result.returncode == 1
