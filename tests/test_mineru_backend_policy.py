"""Tests for the pipeline-only MinerU backend policy."""

import argparse
from pathlib import Path

import convert_research_doc as m


def _args(**overrides):
    base = dict(
        ocr="auto",
        mineru_backend="auto",
        mineru_batch_size=0,
        mineru_timeout_seconds=0,
        mineru_stall_timeout_seconds=0,
        mineru_heartbeat_seconds=0,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def _patch_common(monkeypatch, calls, result=0):
    monkeypatch.setattr(m, "command_path", lambda name: "/fake/mineru" if name == "mineru" else None)

    def fake_run(command, **kwargs):
        backend = command[command.index("-b") + 1]
        calls.append(backend)
        return result, "", ""

    monkeypatch.setattr(m, "run_command_streaming", fake_run)


def test_auto_backend_uses_pipeline(monkeypatch, tmp_path):
    calls = []
    _patch_common(monkeypatch, calls)
    result = m.run_mineru(Path("doc.docx"), tmp_path / "raw", _args())
    assert calls == ["pipeline"]
    assert result.returncode == 0
    assert any("auto-selected: pipeline" in w for w in result.warnings)


def test_explicit_pipeline_uses_pipeline(monkeypatch, tmp_path):
    calls = []
    _patch_common(monkeypatch, calls)
    result = m.run_mineru(
        Path("doc.docx"), tmp_path / "raw", _args(mineru_backend="pipeline")
    )
    assert calls == ["pipeline"]
    assert result.returncode == 0


def test_resolver_rejects_vlm_and_hybrid_backends():
    for backend in ("hybrid-engine", "vlm-engine", "vlm-http-client"):
        try:
            m.resolve_mineru_backend(backend)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected {backend} to be rejected")
