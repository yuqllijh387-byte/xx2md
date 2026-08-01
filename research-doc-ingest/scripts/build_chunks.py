#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


PAGE_HEADING_RE = re.compile(r"^## Page (?P<page>\d+)\b", flags=re.MULTILINE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build page-level JSONL chunks from a Markdown artifact.")
    parser.add_argument("package_dir", help="Converted document package directory.")
    parser.add_argument("--content", default="content.md", help="Markdown file inside package directory.")
    parser.add_argument("--output", default="chunks.jsonl", help="Output JSONL file inside package directory.")
    parser.add_argument("--type", default="markdown", help="Chunk type value.")
    return parser.parse_args()


def source_from_structure(package_dir: Path) -> str:
    structure_path = package_dir / "structure.json"
    if not structure_path.exists():
        return ""
    try:
        data = json.loads(structure_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    return str(data.get("source", ""))


def page_chunks(content: str) -> list[tuple[int | None, str]]:
    matches = list(PAGE_HEADING_RE.finditer(content))
    chunks: list[tuple[int | None, str]] = []
    if not matches:
        return [(None, content.strip())]
    if matches[0].start() > 0:
        preface = content[: matches[0].start()].strip()
        if preface:
            chunks.append((None, preface))
    for index, match in enumerate(matches):
        page = int(match.group("page"))
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        text = content[match.start() : end].strip()
        if text:
            chunks.append((page, text))
    return chunks


def main() -> int:
    args = parse_args()
    package_dir = Path(args.package_dir).resolve()
    content_path = package_dir / args.content
    output_path = package_dir / args.output
    if not content_path.exists():
        raise SystemExit(f"content file not found: {content_path}")

    source = source_from_structure(package_dir)
    chunks = page_chunks(content_path.read_text(encoding="utf-8", errors="replace"))
    with output_path.open("w", encoding="utf-8") as fh:
        for index, (page, text) in enumerate(chunks):
            row = {
                "chunk_index": index,
                "source": source,
                "page_or_slide": f"page:{page}" if page is not None else None,
                "type": args.type,
                "text": text,
                "derived_from": args.content,
            }
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"Wrote chunks: {output_path}")
    print(f"Chunk count: {len(chunks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
