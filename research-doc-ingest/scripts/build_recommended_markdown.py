#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


BOILERPLATE_LINES = {
    "内部使用——严格保密",
    "内部使用—严格保密",
    "内部使用-严格保密",
    "严格保密",
    "confidential",
    "strictly confidential",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build one clean recommended Markdown file from page-semantic JSONL files."
    )
    parser.add_argument("package_dir", help="Converted document package directory.")
    parser.add_argument(
        "--page-semantics",
        action="append",
        required=True,
        help="Page-semantic JSONL file inside the package. Repeat in priority order.",
    )
    parser.add_argument(
        "--output-content",
        default="recommended.md",
        help="Final Markdown filename inside the package.",
    )
    parser.add_argument("--title", default=None, help="Document title.")
    parser.add_argument(
        "--expected-pages",
        type=int,
        default=None,
        help="Require complete coverage from page 1 through this page.",
    )
    return parser.parse_args()


def load_described_pages(paths: list[Path]) -> dict[int, str]:
    pages: dict[int, str] = {}
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("status") != "described":
                continue
            page = int(row["page"])
            description = str(row.get("description") or "").strip()
            if description and page not in pages:
                pages[page] = description
    return pages


def unwrap_code_fence(text: str) -> str:
    lines = text.strip().splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def is_boilerplate(line: str) -> bool:
    stripped = line.strip()
    content = re.sub(r"^(?:(?:>\s*)|(?:[-*+]\s+))+", "", stripped)
    normalized = re.sub(r"\s+", " ", content).lower()
    if normalized in BOILERPLATE_LINES:
        return True
    return bool(re.fullmatch(r"(?:page|第)\s*\d+\s*(?:页)?", normalized))


def clean_page_markdown(text: str) -> str:
    lines: list[str] = []
    for line in unwrap_code_fence(text).splitlines():
        if is_boilerplate(line):
            continue
        if "推荐 Markdown" in line and line.lstrip().startswith("#"):
            continue
        heading = re.match(r"^(#{1,5})(\s+.+)$", line)
        lines.append(f"##{line}" if heading else line)

    compact: list[str] = []
    for line in lines:
        if not line.strip() and (not compact or not compact[-1].strip()):
            continue
        compact.append(line.rstrip())
    return "\n".join(compact).strip()


def main() -> int:
    args = parse_args()
    package_dir = Path(args.package_dir).resolve()
    semantic_paths = [package_dir / value for value in args.page_semantics]
    missing_files = [str(path) for path in semantic_paths if not path.exists()]
    if missing_files:
        raise SystemExit("page-semantic files not found: " + ", ".join(missing_files))

    pages = load_described_pages(semantic_paths)
    if not pages:
        raise SystemExit("no described page-semantic rows found")

    expected_pages = args.expected_pages or max(pages)
    missing_pages = [page for page in range(1, expected_pages + 1) if page not in pages]
    if missing_pages:
        raise SystemExit(
            "refusing incomplete final Markdown; missing pages: "
            + ", ".join(str(page) for page in missing_pages)
        )

    title = args.title or package_dir.name
    output: list[str] = [f"# {title}", ""]
    for page in range(1, expected_pages + 1):
        markdown = clean_page_markdown(pages[page])
        output.extend([f"## 第 {page} 页", "", markdown or "_本页无实质内容。_", ""])

    output_path = package_dir / args.output_content
    output_path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    print(f"Wrote recommended Markdown: {output_path}")
    print(f"Pages: {expected_pages}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
