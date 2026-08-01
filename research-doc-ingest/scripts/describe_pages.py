#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


PAGE_HEADING_RE = re.compile(r"^## Page (?P<page>\d+)\b", flags=re.MULTILINE)


@dataclass
class PageInput:
    page: int
    text: str
    image_path: Path
    action: str = "rewrite"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run page-level multimodal semantic understanding for converted research document packages."
    )
    parser.add_argument("package_dir", help="Converted document package directory.")
    parser.add_argument(
        "--source",
        default=None,
        help="Source PDF path. Defaults to structure.json source.",
    )
    parser.add_argument(
        "--pages",
        default=None,
        help="Pages to process, for example 4,6,8 or 4-8.",
    )
    parser.add_argument(
        "--selection",
        default=None,
        help="Selection JSON from select_semantic_pages.py. Processes augment/rewrite pages.",
    )
    parser.add_argument(
        "--action",
        choices=["augment", "rewrite"],
        default="rewrite",
        help="Action for explicitly specified --pages. Ignored when --selection is used.",
    )
    parser.add_argument(
        "--provider",
        choices=["dry-run", "openai"],
        default="dry-run",
        help="Provider for page understanding.",
    )
    parser.add_argument(
        "--models",
        default=os.getenv("RESEARCH_DOC_PAGE_MODELS", "qwen3.7-plus"),
        help="Comma-separated model names.",
    )
    parser.add_argument(
        "--api-key-env",
        default=os.getenv("RESEARCH_DOC_API_KEY_ENV", "OPENAI_API_KEY"),
        help="Environment variable that contains the API key.",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("OPENAI_BASE_URL") or os.getenv("RESEARCH_DOC_BASE_URL"),
        help="Optional OpenAI-compatible base URL.",
    )
    parser.add_argument(
        "--api-mode",
        choices=["chat"],
        default="chat",
        help="OpenAI-compatible API mode. Page understanding currently uses Chat Completions.",
    )
    parser.add_argument(
        "--allow-external",
        action="store_true",
        help="Allow sending rendered page images to an external API provider.",
    )
    parser.add_argument(
        "--render-dpi",
        type=int,
        default=180,
        help="DPI for rendering PDF pages.",
    )
    parser.add_argument(
        "--max-page-text-chars",
        type=int,
        default=9000,
        help="Maximum MinerU page text characters included in the prompt.",
    )
    parser.add_argument(
        "--output-prefix",
        default="page_semantics_sample",
        help="Output file prefix inside the package directory.",
    )
    parser.add_argument(
        "--prompt-mode",
        choices=["full", "markdown-only"],
        default="full",
        help="full writes analysis sections; markdown-only writes only final semantic Markdown.",
    )
    parser.add_argument(
        "--omit-boilerplate",
        action="store_true",
        help="Omit headers, footers, watermarks, logos, confidentiality labels, and page numbers unless content-bearing.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Number of concurrent external model calls.",
    )
    return parser.parse_args()


def parse_pages(spec: str) -> list[int]:
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            left, right = part.split("-", 1)
            start = int(left.strip())
            end = int(right.strip())
            if start > end:
                raise ValueError(f"invalid page range: {part}")
            pages.update(range(start, end + 1))
        else:
            pages.add(int(part))
    if not pages:
        raise ValueError("no pages specified")
    return sorted(pages)


def load_selection(package_dir: Path, value: str) -> dict[int, str]:
    path = package_dir / value
    if not path.exists():
        raise SystemExit(f"semantic selection not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    actions: dict[int, str] = {}
    for row in payload.get("pages", []):
        action = str(row.get("action") or "")
        if action not in {"augment", "rewrite"}:
            continue
        actions[int(row["page"])] = action
    if not actions:
        raise SystemExit("semantic selection did not contain augment/rewrite pages")
    return actions


def load_source_path(package_dir: Path, explicit_source: str | None) -> Path:
    if explicit_source:
        source = Path(explicit_source)
    else:
        structure_path = package_dir / "structure.json"
        if not structure_path.exists():
            raise SystemExit("structure.json not found; pass --source explicitly")
        structure = json.loads(structure_path.read_text(encoding="utf-8"))
        source = Path(str(structure.get("source", "")))
    if not source.exists():
        raise SystemExit(f"source PDF not found: {source}")
    return source.resolve()


def extract_page_texts(content: str) -> dict[int, str]:
    matches = list(PAGE_HEADING_RE.finditer(content))
    pages: dict[int, str] = {}
    for index, match in enumerate(matches):
        page = int(match.group("page"))
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        pages[page] = content[start:end].strip()
    return pages


def compact_text(text: str, max_chars: int) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    if len(text) <= max_chars:
        return text
    return text[: max_chars // 2] + "\n\n[...middle omitted...]\n\n" + text[-max_chars // 2 :]


def render_pages(source_pdf: Path, package_dir: Path, pages: list[int], dpi: int) -> dict[int, Path]:
    import fitz  # type: ignore

    output_dir = package_dir / "assets" / "page_semantics"
    output_dir.mkdir(parents=True, exist_ok=True)
    rendered: dict[int, Path] = {}
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    with fitz.open(str(source_pdf)) as doc:
        for page in pages:
            if page < 1 or page > doc.page_count:
                raise SystemExit(f"page {page} out of range 1..{doc.page_count}")
            image_path = output_dir / f"page_{page:03d}_{dpi}dpi.png"
            if not image_path.exists():
                pix = doc.load_page(page - 1).get_pixmap(matrix=matrix, alpha=False)
                pix.save(str(image_path))
            rendered[page] = image_path
    return rendered


def data_url_for_image(path: Path) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def page_prompt(page_input: PageInput, prompt_mode: str, omit_boilerplate: bool) -> str:
    boilerplate_rule = (
        "Omit page headers, footers, watermarks, logos, confidentiality labels, and page numbers unless they contain substantive document content."
        if omit_boilerplate
        else "Keep source traceability text when it is content-bearing; treat headers, footers, and watermarks as lower-priority metadata."
    )
    if prompt_mode == "markdown-only" and page_input.action == "augment":
        return f"""You are adding missing visual semantics to one MinerU-converted page.

Use the full page image to understand charts, diagrams, flows, architecture, callouts, and relationships.
The MinerU Markdown below remains the authoritative source for prose, numbers, and tables.

Write Chinese Markdown only. Do not wrap the answer in code fences.
Return one compact section headed `### 视觉与版面语义`.
Do not repeat the page title, prose, or tables already present in MinerU.
Do not emit Markdown image links or asset paths.
{boilerplate_rule}

Rules:
- Explain only substantive information that MinerU text/table extraction does not already express.
- For charts, record visible axes, legends, key values, trend, comparison, and supported conclusion.
- For diagrams and multi-panel layouts, record nodes, arrows, sequence, grouping, and image-text relationships.
- Preserve visible labels, dates, units, and numbers exactly. Never move a value to another label or series.
- If a value or relationship is unclear, omit that detail rather than infer or invent it.
- If MinerU already captures all substantive information, return `### 视觉与版面语义` followed by `无需补充。`

MinerU extracted page Markdown:

{page_input.text}
"""
    if prompt_mode == "markdown-only":
        return f"""You are converting one full page of a research/business PDF into final AI-ready Markdown.

Use the full page image as the primary source for layout and visual relationships.
Use the MinerU page text below as extracted evidence, but correct its structural mistakes when the page image shows a different relationship.

Write Chinese Markdown only. Do not wrap the answer in code fences. Do not include analysis headings such as "页面主标题", "正文层级", "图文关系", "推荐 Markdown", or "不确定性".
{boilerplate_rule}

Rules:
- Start with the real page title as a Markdown heading.
- Preserve important values, percentages, dates, units, table rows, chart labels, axes, legends, and KPI cards.
- Do not use a numeric KPI value as a heading unless it is genuinely the page or section title.
- Represent KPI/callout cards as bullets or blockquotes, for example `> **42%**\\n> 目标指标占比...`.
- Convert important tables into Markdown tables when readable.
- Summarize charts/diagrams with their visible labels, values, and conclusion.
- Do not emit Markdown image links or asset paths.
- Exclude decorative arrows, dividers, background watermarks, and purely branding elements.
- If a value is unreadable, write `未读清` rather than inventing it.

MinerU extracted page text:

{page_input.text}
"""
    return f"""You are converting one full page of a research/business PDF into AI-ready Markdown.

Use the full page image as the primary source for layout and visual relationships.
Use the MinerU page text below as extracted evidence, but correct its structural mistakes when the page image shows a different relationship.

Write Chinese Markdown. Do not wrap the answer in code fences. Do not invent unreadable text or values.
{boilerplate_rule}

Required output sections:

## Page {page_input.page} 语义结构

- 页面主标题:
- 页面功能: market sizing, pricing model, comparison, process, pipeline, financial forecast, IPO plan, or other.
- 正文层级:
  - Distinguish real section titles from KPI cards, callouts, captions, footers, and decorative symbols.
- 关键指标卡:
  - For each KPI/callout, write `指标名称: 数值` and include scope/source text when visible.
- 图表/表格:
  - Summarize each major chart or table with visible labels, values, axes, and the conclusion it supports.
- 图文关系:
  - Explain how page title, KPI cards, charts/tables, and notes support the page argument.
- 推荐 Markdown:
  - Rewrite this page as semantic Markdown. Preserve important values. Do not use numeric values as headings unless they are actual section titles.
- 不确定性:
  - List unreadable regions, ambiguous labels, or conflicts between image and extracted text.

MinerU extracted page text:

{page_input.text}
"""


def openai_client(api_key_env: str, base_url: str | None):
    from openai import OpenAI  # type: ignore

    api_key = os.getenv(api_key_env)
    if not api_key:
        raise RuntimeError(f"{api_key_env} is not set")
    kwargs: dict[str, str] = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


def describe_page_with_chat(client, model: str, page_input: PageInput, prompt_mode: str, omit_boilerplate: bool) -> str:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": page_prompt(page_input, prompt_mode, omit_boilerplate)},
                    {"type": "image_url", "image_url": {"url": data_url_for_image(page_input.image_path)}},
                ],
            }
        ],
    )
    choices = getattr(response, "choices", []) or []
    if not choices:
        raise RuntimeError("response did not contain choices")
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None)
    if isinstance(content, str) and content.strip():
        return clean_model_text(content)
    raise RuntimeError("response did not contain text")


def clean_model_text(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def is_provider_blocking_error(exc: Exception) -> bool:
    text = str(exc).lower()
    markers = [
        "allocationquota.freetieronly",
        "free quota has been exhausted",
        "insufficient_quota",
        "invalid_api_key",
        "unauthorized",
        "permissiondenied",
    ]
    return any(marker in text for marker in markers)


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_markdown(path: Path, package_dir: Path, rows: list[dict[str, object]]) -> None:
    lines = [
        "# Page Semantic Understanding",
        "",
        f"- Created at: {datetime.now(timezone.utc).isoformat()}",
        f"- Package: `{package_dir}`",
        "",
    ]
    for row in rows:
        lines.extend(
            [
                f"## Model: {row['model']} | Page {row['page']}",
                "",
                f"- Status: {row['status']}",
                f"- Page image: `{row['page_image']}`",
                "",
            ]
        )
        if row.get("error"):
            lines.extend(["### Error", "", str(row["error"]), ""])
        else:
            lines.extend([str(row.get("description", "")).strip(), ""])
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_audit(path: Path, args: argparse.Namespace, rows: list[dict[str, object]], elapsed_seconds: float) -> None:
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status", "unknown"))
        counts[status] = counts.get(status, 0) + 1
    lines = [
        "# Page Semantic Audit",
        "",
        f"- Created at: {datetime.now(timezone.utc).isoformat()}",
        f"- Provider: {args.provider}",
        f"- Models: {args.models}",
        f"- API mode: {args.api_mode}",
        f"- API key env: {args.api_key_env}",
        f"- Base URL configured: {'yes' if args.base_url else 'no'}",
        f"- Render DPI: {args.render_dpi}",
        f"- Prompt mode: {args.prompt_mode}",
        f"- Omit boilerplate: {'yes' if args.omit_boilerplate else 'no'}",
        f"- Concurrency: {args.concurrency}",
        f"- Elapsed seconds: {elapsed_seconds:.3f}",
        f"- Total rows: {len(rows)}",
    ]
    for status, count in sorted(counts.items()):
        lines.append(f"- {status}: {count}")
    lines.extend(["", "## Notes", ""])
    if args.provider == "dry-run":
        lines.append("- Dry run only: no page image was sent to an external model.")
    else:
        lines.append("- External visual model was used; verify confidentiality requirements.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    started = time.perf_counter()
    args = parse_args()
    package_dir = Path(args.package_dir).resolve()
    content_path = package_dir / "content.md"
    if not content_path.exists():
        raise SystemExit(f"content.md not found: {content_path}")
    if args.provider == "openai" and not args.allow_external:
        raise SystemExit("Refusing external API call: pass --allow-external to send page images.")
    if args.provider == "openai" and not os.getenv(args.api_key_env):
        raise SystemExit(f"{args.api_key_env} is not set.")

    if bool(args.pages) == bool(args.selection):
        raise SystemExit("pass exactly one of --pages or --selection")
    if args.selection:
        page_actions = load_selection(package_dir, args.selection)
        pages = sorted(page_actions)
    else:
        pages = parse_pages(args.pages)
        page_actions = {page: args.action for page in pages}
    models = [model.strip() for model in args.models.split(",") if model.strip()]
    if not models:
        raise SystemExit("no models specified")

    source_pdf = load_source_path(package_dir, args.source)
    page_texts = extract_page_texts(content_path.read_text(encoding="utf-8", errors="replace"))
    rendered_pages = render_pages(source_pdf, package_dir, pages, args.render_dpi)

    tasks = [(model, page) for model in models for page in pages]

    def process_task(model: str, page: int) -> dict[str, object]:
        task_started = time.perf_counter()
        page_input = PageInput(
            page=page,
            text=compact_text(page_texts.get(page, ""), args.max_page_text_chars),
            image_path=rendered_pages[page],
            action=page_actions[page],
        )
        row: dict[str, object] = {
            "model": model,
            "page": page,
            "status": "pending",
            "page_image": str(page_input.image_path.relative_to(package_dir)),
            "image_bytes": page_input.image_path.stat().st_size,
            "prompt_mode": args.prompt_mode,
            "action": page_input.action,
            "omit_boilerplate": args.omit_boilerplate,
            "description": None,
            "error": None,
        }
        if args.provider == "dry-run":
            row["status"] = "pending"
            row["elapsed_seconds"] = round(time.perf_counter() - task_started, 3)
            return row
        try:
            client = openai_client(args.api_key_env, args.base_url)
            row["description"] = describe_page_with_chat(
                client,
                model,
                page_input,
                args.prompt_mode,
                args.omit_boilerplate,
            )
            row["status"] = "described"
        except Exception as exc:
            row["status"] = "error"
            row["error"] = str(exc)
        row["elapsed_seconds"] = round(time.perf_counter() - task_started, 3)
        return row

    jsonl_path = package_dir / f"{args.output_prefix}.jsonl"
    md_path = package_dir / f"{args.output_prefix}.md"
    audit_path = package_dir / f"{args.output_prefix}_audit.md"
    rows: list[dict[str, object]] = []

    def persist_checkpoint() -> None:
        rows.sort(key=lambda item: (str(item.get("model")), int(item.get("page", 0))))
        elapsed_seconds = time.perf_counter() - started
        write_jsonl(jsonl_path, rows)
        write_markdown(md_path, package_dir, rows)
        write_audit(audit_path, args, rows, elapsed_seconds)

    if args.concurrency <= 1 or len(tasks) <= 1:
        for index, (model, page) in enumerate(tasks, start=1):
            row = process_task(model, page)
            rows.append(row)
            persist_checkpoint()
            print(f"[{index}/{len(tasks)}] [{model}] page {page}: {row['status']} ({row['elapsed_seconds']}s)", flush=True)
            if row["status"] == "error" and row.get("error") and is_provider_blocking_error(Exception(str(row["error"]))):
                break
    else:
        completed = 0
        with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as executor:
            future_map = {executor.submit(process_task, model, page): (model, page) for model, page in tasks}
            for future in as_completed(future_map):
                model, page = future_map[future]
                row = future.result()
                rows.append(row)
                persist_checkpoint()
                completed += 1
                print(f"[{completed}/{len(tasks)}] [{model}] page {page}: {row['status']} ({row['elapsed_seconds']}s)", flush=True)

    persist_checkpoint()
    print(f"Wrote Markdown: {md_path}")
    print(f"Wrote JSONL: {jsonl_path}")
    print(f"Wrote audit: {audit_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
