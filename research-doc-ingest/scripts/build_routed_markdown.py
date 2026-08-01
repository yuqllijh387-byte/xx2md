#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import re
from pathlib import Path


PAGE_HEADING_RE = re.compile(r"^## Page (?P<page>\d+)\b", flags=re.MULTILINE)
VISUAL_PLACEHOLDER_RE = re.compile(r"^\*\*Visual element \d+:.*\*\*$")
GENERIC_VISUAL_HEADING_RE = re.compile(r"^###\s+(?:Image|Chart):\s+untitled\s+(?:visual|chart)$", re.I)
MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build final Markdown by routing MinerU pages through keep, augment, or rewrite."
    )
    parser.add_argument("package_dir", help="Converted document package directory.")
    parser.add_argument("--selection", required=True, help="Selection JSON inside the package.")
    parser.add_argument(
        "--page-semantics",
        action="append",
        required=True,
        help="Selected-page JSONL inside the package. Repeat to add incremental results.",
    )
    parser.add_argument(
        "--source-content",
        default="content.md",
        help="MinerU Markdown inside the package.",
    )
    parser.add_argument(
        "--output-content",
        default="recommended.routed.md",
        help="Final Markdown filename inside the package.",
    )
    parser.add_argument(
        "--audit-output",
        default="recommended.routed.audit.md",
        help="Routing audit filename inside the package.",
    )
    parser.add_argument("--title", default=None, help="Final document title.")
    return parser.parse_args()


def page_blocks(content: str) -> dict[int, str]:
    matches = list(PAGE_HEADING_RE.finditer(content))
    blocks: dict[int, str] = {}
    for index, match in enumerate(matches):
        page = int(match.group("page"))
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        blocks[page] = content[start:end].strip()
    return blocks


def unwrap_code_fence(text: str) -> str:
    lines = text.strip().splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def normalize_visible_line(line: str) -> str:
    text = line.strip()
    text = re.sub(r"^[#>*+\-\s]+", "", text)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    text = text.strip("*_ ")
    return re.sub(r"\s+", " ", text)


def is_confidentiality_label(line: str) -> bool:
    normalized = normalize_visible_line(line).lower()
    return (
        "内部使用" in normalized
        or "严格保密" in normalized
        or normalized in {"confidential", "strictly confidential"}
    )


def is_branding_or_confidentiality(line: str) -> bool:
    return is_confidentiality_label(line)


def clean_mineru_page(block: str) -> str:
    source_lines = block.splitlines()
    output: list[str] = []
    index = 0
    while index < len(source_lines):
        line = source_lines[index]
        stripped = line.strip()

        if VISUAL_PLACEHOLDER_RE.match(stripped):
            while output and not output[-1].strip():
                output.pop()
            if output and GENERIC_VISUAL_HEADING_RE.match(output[-1].strip()):
                output.pop()
            index += 1
            while index < len(source_lines):
                candidate = source_lines[index].strip()
                if not candidate or candidate.startswith(
                    ("- Asset:", "- Textual description:", "- Uncertainty:")
                ):
                    index += 1
                    continue
                break
            continue

        if GENERIC_VISUAL_HEADING_RE.match(stripped):
            index += 1
            continue
        if re.match(r"^\*header:", stripped, flags=re.I):
            index += 1
            continue
        if re.match(r"^\*page_number:", stripped, flags=re.I):
            index += 1
            continue
        footer_match = re.match(r"^\*footer:\s*(.*?)\*$", stripped, flags=re.I)
        if footer_match:
            footer = footer_match.group(1).strip()
            if footer.startswith(("资料来源", "来源：", "来源:")) and not is_confidentiality_label(
                footer
            ):
                output.append(f"> {footer}")
            index += 1
            continue
        if is_confidentiality_label(stripped):
            index += 1
            continue
        if MARKDOWN_IMAGE_RE.search(stripped):
            index += 1
            continue
        if stripped == "### Table: untitled table":
            output.append("### 表格")
            index += 1
            continue
        output.append(line.rstrip())
        index += 1

    return compact_blank_lines(output)


def clean_semantic_markdown(text: str, action: str) -> str:
    output: list[str] = []
    for line in unwrap_code_fence(text).splitlines():
        stripped = line.strip()
        if not stripped or not MARKDOWN_IMAGE_RE.search(stripped):
            if is_branding_or_confidentiality(stripped):
                continue
            if MARKDOWN_IMAGE_RE.search(stripped):
                continue
            heading = re.match(r"^(#{1,6})\s+(.+)$", line)
            if heading:
                level = len(heading.group(1))
                if action == "rewrite":
                    level = max(3, level + 2)
                else:
                    level = max(3, level)
                output.append("#" * min(level, 6) + " " + heading.group(2).strip())
            else:
                output.append(line.rstrip())
    cleaned = compact_blank_lines(output)
    if "无需补充" in cleaned and len(cleaned) < 80:
        return ""
    return cleaned


def compact_blank_lines(lines: list[str]) -> str:
    compact: list[str] = []
    for line in lines:
        if not line.strip() and (not compact or not compact[-1].strip()):
            continue
        compact.append(line.rstrip())
    return "\n".join(compact).strip()


def load_semantics(paths: list[Path]) -> dict[int, dict[str, object]]:
    rows: dict[int, dict[str, object]] = {}
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("status") != "described":
                continue
            rows[int(row["page"])] = row
    return rows


def numeric_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for match in re.finditer(
        r"(?<![A-Za-z0-9,])\d[\d,]*(?:\.\d+)?%?(?![A-Za-z0-9,])",
        text,
    ):
        token = match.group(0).replace(",", "")
        digits = re.sub(r"\D", "", token)
        if len(digits) >= 2 or "." in token or "%" in token:
            tokens.add(token)
    return tokens


def novel_numeric_tokens(generated: str, evidence: str) -> list[str]:
    source = numeric_tokens(evidence)
    return sorted(numeric_tokens(generated) - source)


def source_pdf_path(package_dir: Path) -> Path:
    structure = json.loads((package_dir / "structure.json").read_text(encoding="utf-8"))
    source = Path(str(structure.get("source") or ""))
    if not source.exists():
        raise SystemExit(f"source PDF not found: {source}")
    return source.resolve()


def write_audit(
    path: Path,
    selection: dict[str, object],
    output_name: str,
    records: list[dict[str, object]],
) -> None:
    statuses = Counter(str(record["status"]) for record in records)
    lines = [
        "# Routed Markdown Audit",
        "",
        f"- Created at: {datetime.now(timezone.utc).isoformat()}",
        f"- Sensitivity: {selection['sensitivity']}",
        f"- Risk threshold: {selection['risk_threshold']}",
        f"- Output: {output_name}",
        f"- Pages: {len(records)}",
        "- Statuses: " + ", ".join(f"{key}={value}" for key, value in sorted(statuses.items())),
        "",
        "## Pages",
        "",
    ]
    for record in records:
        novel = record.get("novel_numeric_tokens") or []
        novel_text = ",".join(str(token) for token in novel) if novel else "none"
        lines.append(
            f"- Page {record['page']}: route={record['route']}; status={record['status']}; "
            f"score={record['score']}; novel_numbers={novel_text}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    package_dir = Path(args.package_dir).resolve()
    selection_path = package_dir / args.selection
    semantics_paths = [package_dir / value for value in args.page_semantics]
    content_path = package_dir / args.source_content
    for path in (selection_path, *semantics_paths, content_path):
        if not path.exists():
            raise SystemExit(f"required input not found: {path}")

    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    routes = {int(row["page"]): row for row in selection.get("pages", [])}
    expected_pages = int(selection.get("expected_pages") or len(routes))
    original_pages = page_blocks(content_path.read_text(encoding="utf-8", errors="replace"))
    semantics = load_semantics(semantics_paths)
    missing_original = [page for page in range(1, expected_pages + 1) if page not in original_pages]
    if missing_original:
        raise SystemExit("MinerU content is missing pages: " + ",".join(map(str, missing_original)))

    import fitz  # type: ignore

    source = source_pdf_path(package_dir)
    title = args.title or source.stem
    output: list[str] = [f"# {title}", ""]
    records: list[dict[str, object]] = []

    with fitz.open(str(source)) as doc:
        for page in range(1, expected_pages + 1):
            route = routes.get(page, {"action": "keep", "score": 0})
            action = str(route.get("action") or "keep")
            score = int(route.get("score") or 0)
            mineru = clean_mineru_page(original_pages[page])
            body = mineru
            status = "kept_mineru"
            novel: list[str] = []

            if action in {"augment", "rewrite"}:
                semantic_row = semantics.get(page)
                if semantic_row is None:
                    status = "fallback_missing_semantics"
                else:
                    generated = clean_semantic_markdown(
                        str(semantic_row.get("description") or ""),
                        action,
                    )
                    evidence = original_pages[page] + "\n" + doc.load_page(page - 1).get_text("text")
                    novel = novel_numeric_tokens(generated, evidence)
                    if action == "augment":
                        if generated:
                            body = mineru.rstrip() + "\n\n" + generated
                            status = "augmented_numeric_warning" if novel else "augmented"
                        else:
                            status = "kept_no_missing_visual_semantics"
                    elif generated:
                        body = generated
                        status = "rewritten_numeric_warning" if novel else "rewritten"
                    else:
                        status = "fallback_empty_rewrite"

            output.extend([f"## 第 {page} 页", "", body or "_本页无实质内容。_", ""])
            records.append(
                {
                    "page": page,
                    "route": action,
                    "score": score,
                    "status": status,
                    "novel_numeric_tokens": novel,
                }
            )

    output_path = package_dir / args.output_content
    audit_path = package_dir / args.audit_output
    output_path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    write_audit(audit_path, selection, args.output_content, records)

    counts = Counter(str(record["status"]) for record in records)
    print(f"Wrote routed Markdown: {output_path}")
    print(f"Wrote routing audit: {audit_path}")
    print("Statuses: " + ", ".join(f"{key}={value}" for key, value in sorted(counts.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
