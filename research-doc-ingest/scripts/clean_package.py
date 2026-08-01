#!/usr/bin/env python3
"""Reduce a converted package to the minimal daily-use artifacts.

Keeps only:
- the accepted Markdown (the routed final Markdown when present, otherwise
  content.md), and
- chunks.jsonl rebuilt from that Markdown.

Everything else in the package directory (structure.json, audits, baseline,
selection files, page semantics, assets/, engine_output/, .engine_work/) is
removed. Run this only after the package has been reviewed and accepted:
deletion is irreversible.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

from build_chunks import page_chunks, source_from_structure


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Keep only the accepted Markdown and chunks.jsonl; remove all other package files."
    )
    parser.add_argument("package_dir", help="Converted document package directory.")
    parser.add_argument(
        "--keep",
        default=None,
        help="Markdown filename inside the package to keep. "
        "Default: a single *.final.md / content.integrated.md when present, else content.md.",
    )
    parser.add_argument(
        "--chunks-output",
        default="chunks.jsonl",
        help="Chunks filename inside the package directory.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be removed without deleting anything.",
    )
    return parser.parse_args()


def choose_markdown(package_dir: Path, keep: str | None) -> Path:
    if keep:
        candidate = package_dir / keep
        if not candidate.exists():
            raise SystemExit(f"requested markdown not found: {candidate}")
        return candidate
    candidates = sorted(package_dir.glob("*.final.md"))
    integrated = package_dir / "content.integrated.md"
    if integrated.exists():
        candidates.append(integrated)
    if len(candidates) > 1:
        names = ", ".join(path.name for path in candidates)
        raise SystemExit(f"multiple final markdown candidates ({names}); pass --keep")
    if candidates:
        return candidates[0]
    fallback = package_dir / "content.md"
    if not fallback.exists():
        raise SystemExit(f"no markdown artifact found in {package_dir}")
    return fallback


def rebuild_chunks(package_dir: Path, markdown_path: Path, chunks_name: str) -> tuple[Path, int]:
    source = source_from_structure(package_dir)
    chunks = page_chunks(markdown_path.read_text(encoding="utf-8", errors="replace"))
    chunks_path = package_dir / chunks_name
    with chunks_path.open("w", encoding="utf-8") as fh:
        for index, (page, text) in enumerate(chunks):
            row = {
                "chunk_index": index,
                "source": source,
                "page_or_slide": f"page:{page}" if page is not None else None,
                "type": "markdown",
                "text": text,
                "derived_from": markdown_path.name,
            }
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return chunks_path, len(chunks)


def clean_package(
    package_dir: Path,
    keep: str | None = None,
    chunks_output: str = "chunks.jsonl",
    dry_run: bool = False,
) -> dict[str, object]:
    package_dir = package_dir.resolve()
    if not package_dir.is_dir():
        raise SystemExit(f"package directory not found: {package_dir}")
    markdown_path = choose_markdown(package_dir, keep)
    keep_names = {markdown_path.name, chunks_output}

    if dry_run:
        removed = [
            item.name
            for item in sorted(package_dir.iterdir())
            if item.name not in keep_names
        ]
        return {
            "kept_markdown": markdown_path.name,
            "chunks_output": chunks_output,
            "removed": removed,
            "dry_run": True,
        }

    chunks_path, chunk_count = rebuild_chunks(package_dir, markdown_path, chunks_output)
    removed = []
    for item in sorted(package_dir.iterdir()):
        if item.name in keep_names:
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()
        removed.append(item.name)
    return {
        "kept_markdown": markdown_path.name,
        "chunks_output": chunks_path.name,
        "chunk_count": chunk_count,
        "removed": removed,
        "dry_run": False,
    }


def main() -> int:
    args = parse_args()
    result = clean_package(
        Path(args.package_dir),
        keep=args.keep,
        chunks_output=args.chunks_output,
        dry_run=args.dry_run,
    )
    removed = list(result["removed"])  # type: ignore[arg-type]
    if result["dry_run"]:
        print(f"[dry-run] Would keep: {result['kept_markdown']}, {result['chunks_output']}")
        print(f"[dry-run] Would remove {len(removed)} entries:")
        for name in removed:
            print(f"  - {name}")
        return 0
    print(f"Kept markdown: {result['kept_markdown']}")
    print(f"Rebuilt chunks: {result['chunks_output']} ({result['chunk_count']} chunks)")
    print(f"Removed {len(removed)} entries:")
    for name in removed:
        print(f"  - {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
