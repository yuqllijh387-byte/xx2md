#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mark a converted document package as a baseline.")
    parser.add_argument("package_dir", help="Converted document package directory.")
    parser.add_argument("--label", default="mineru-baseline", help="Baseline label.")
    parser.add_argument("--output", default="baseline.json", help="Baseline file name.")
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        return sum(1 for _ in fh)


def count_pattern(path: Path, pattern: str) -> int:
    if not path.exists():
        return 0
    rx = re.compile(pattern, flags=re.MULTILINE)
    return len(rx.findall(path.read_text(encoding="utf-8", errors="replace")))


def main() -> int:
    args = parse_args()
    package_dir = Path(args.package_dir).resolve()
    if not package_dir.exists():
        raise SystemExit(f"Package directory not found: {package_dir}")

    content = package_dir / "content.md"
    chunks = package_dir / "chunks.jsonl"
    structure = package_dir / "structure.json"
    audit = package_dir / "audit.md"
    assets_dir = package_dir / "assets"
    engine_output = package_dir / "engine_output"

    artifact_paths = [content, chunks, structure, audit]
    artifacts = {
        path.name: {
            "exists": path.exists(),
            "bytes": path.stat().st_size if path.exists() else 0,
            "sha256": sha256_file(path) if path.exists() else None,
        }
        for path in artifact_paths
    }

    asset_files = sorted(p for p in assets_dir.rglob("*") if p.is_file()) if assets_dir.exists() else []
    raw_files = sorted(p for p in engine_output.rglob("*") if p.is_file()) if engine_output.exists() else []

    data = {
        "label": args.label,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "package_dir": str(package_dir),
        "artifacts": artifacts,
        "counts": {
            "page_headings": count_pattern(content, r"^## Page \d+\b"),
            "visual_placeholders": count_pattern(content, r"^\*\*Visual element \d+:"),
            "chunks": count_lines(chunks),
            "asset_files": len(asset_files),
            "engine_output_files": len(raw_files),
        },
        "asset_extensions": {},
    }
    for path in asset_files:
        suffix = path.suffix.lower() or "<none>"
        data["asset_extensions"][suffix] = data["asset_extensions"].get(suffix, 0) + 1

    out_path = package_dir / args.output
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote baseline: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
