#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


VISUAL_BLOCK_RE = re.compile(
    r"\*\*Visual element (?P<id>\d+): (?P<label>.*?)\*\*\n\n"
    r"- Asset: `(?P<asset>[^`]+)`\n"
    r"- Textual description: TODO review the asset or engine output and replace this note with a complete description\.\n"
    r"- Uncertainty: description not yet verified\.\n?",
    flags=re.MULTILINE,
)

PAGE_HEADING_RE = re.compile(r"^## Page (?P<page>\d+)\b", flags=re.MULTILINE)


@dataclass
class VisualItem:
    visual_id: int
    label: str
    asset: str
    page: int | None
    context_before: str
    context_after: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Describe visual placeholders in a research document package."
    )
    parser.add_argument("package_dir", help="Converted document package directory.")
    parser.add_argument(
        "--provider",
        choices=["auto", "dry-run", "openai"],
        default="auto",
        help="Description provider. auto uses dry-run unless explicitly configured.",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("RESEARCH_DOC_VISION_MODEL", "gpt-4.1-mini"),
        help="Vision model name for provider=openai.",
    )
    parser.add_argument(
        "--api-key-env",
        default="OPENAI_API_KEY",
        help="Environment variable that contains the API key.",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("OPENAI_BASE_URL") or os.getenv("RESEARCH_DOC_BASE_URL"),
        help="Optional OpenAI-compatible base URL.",
    )
    parser.add_argument(
        "--api-mode",
        choices=["auto", "responses", "chat"],
        default=os.getenv("RESEARCH_DOC_API_MODE", "auto"),
        help="OpenAI-compatible API mode. auto tries Responses first, then Chat Completions.",
    )
    parser.add_argument(
        "--allow-external",
        action="store_true",
        help="Allow sending visual assets to an external API provider.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Maximum visuals to process.")
    parser.add_argument(
        "--output-content",
        default="content.visual.md",
        help="Output Markdown file with visual descriptions.",
    )
    parser.add_argument(
        "--descriptions-output",
        default="visual_descriptions.jsonl",
        help="JSONL file for visual description results or pending queue.",
    )
    parser.add_argument(
        "--audit-output",
        default="visual_audit.md",
        help="Markdown audit file for visual description pass.",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Overwrite content.md instead of writing content.visual.md.",
    )
    parser.add_argument(
        "--min-size-bytes",
        type=int,
        default=0,
        help="Skip assets smaller than this size.",
    )
    parser.add_argument(
        "--continue-on-api-error",
        action="store_true",
        help="Continue after provider-level API errors. By default quota/authentication errors stop the run.",
    )
    return parser.parse_args()


def find_page_for_offset(content: str, offset: int) -> int | None:
    page = None
    for match in PAGE_HEADING_RE.finditer(content):
        if match.start() > offset:
            break
        page = int(match.group("page"))
    return page


def compact_context(text: str, max_chars: int = 1200) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def extract_visual_items(package_dir: Path, content: str) -> list[VisualItem]:
    items: list[VisualItem] = []
    matches = list(VISUAL_BLOCK_RE.finditer(content))
    for index, match in enumerate(matches):
        prev_end = matches[index - 1].end() if index else 0
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        context_before = compact_context(content[max(prev_end, match.start() - 2000) : match.start()])
        context_after = compact_context(content[match.end() : min(next_start, match.end() + 1200)])
        items.append(
            VisualItem(
                visual_id=int(match.group("id")),
                label=match.group("label").strip(),
                asset=match.group("asset").strip(),
                page=find_page_for_offset(content, match.start()),
                context_before=context_before,
                context_after=context_after,
            )
        )
    return items


def item_to_json(
    item: VisualItem,
    package_dir: Path,
    status: str,
    description: str | None = None,
    error: str | None = None,
    api_mode: str | None = None,
) -> dict[str, object]:
    asset_path = (package_dir / item.asset).resolve()
    return {
        "visual_id": item.visual_id,
        "status": status,
        "page": item.page,
        "label": item.label,
        "asset": item.asset,
        "asset_exists": asset_path.exists(),
        "asset_bytes": asset_path.stat().st_size if asset_path.exists() else 0,
        "description": description,
        "error": error,
        "api_mode": api_mode,
        "context_before": item.context_before,
        "context_after": item.context_after,
    }


def data_url_for_image(path: Path) -> str:
    suffix = path.suffix.lower()
    media_type = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(suffix, "application/octet-stream")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def visual_prompt(item: VisualItem) -> str:
    return f"""You are describing a visual element from a research/business document for AI ingestion.

Write Chinese Markdown. Be factual and concise. Do not invent unreadable labels or values.
Do not wrap the answer in code fences. Use plain Markdown bullets only.
Separate directly visible facts from inferred conclusions.
When comparing numeric values, verify the ordering. Do not claim highest/lowest unless every visible value supports it.
If the visual values conflict with nearby document text, state the conflict instead of resolving it silently.

Required fields:
- 类型:
- 页码:
- 图中可读文本/标签:
- 结构或坐标轴:
- 主要信息:
- 支撑的结论:
- 不确定性:

Visual metadata:
- visual_id: {item.visual_id}
- page: {item.page}
- label: {item.label}
- asset: {item.asset}

Nearby document context before the visual:
{item.context_before}

Nearby document context after the visual:
{item.context_after}
"""


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


def openai_client(env_name: str, base_url: str | None):
    from openai import OpenAI  # type: ignore

    api_key = os.getenv(env_name)
    if not api_key:
        raise RuntimeError(f"{env_name} is not set")
    client_kwargs: dict[str, str] = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url
    return OpenAI(**client_kwargs)


def describe_with_responses(client, item: VisualItem, image_path: Path, model: str) -> str:
    response = client.responses.create(
        model=model,
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": visual_prompt(item)},
                    {"type": "input_image", "image_url": data_url_for_image(image_path)},
                ],
            }
        ],
    )
    output_text = getattr(response, "output_text", None)
    if output_text:
        return str(output_text).strip()

    chunks: list[str] = []
    for output in getattr(response, "output", []) or []:
        for content in getattr(output, "content", []) or []:
            text = getattr(content, "text", None)
            if text:
                chunks.append(str(text))
    result = "\n".join(chunks).strip()
    if not result:
        raise RuntimeError("Responses API output did not contain text")
    return result


def describe_with_chat(client, item: VisualItem, image_path: Path, model: str) -> str:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": visual_prompt(item)},
                    {"type": "image_url", "image_url": {"url": data_url_for_image(image_path)}},
                ],
            }
        ],
    )
    choices = getattr(response, "choices", []) or []
    if not choices:
        raise RuntimeError("Chat Completions response did not contain choices")
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None)
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        chunks: list[str] = []
        for part in content:
            text = getattr(part, "text", None)
            if not text and isinstance(part, dict):
                text = part.get("text")
            if text:
                chunks.append(str(text))
        result = "\n".join(chunks).strip()
        if result:
            return result
    raise RuntimeError("Chat Completions output did not contain text")


def describe_with_openai(
    item: VisualItem,
    image_path: Path,
    model: str,
    api_key_env: str,
    base_url: str | None,
    api_mode: str,
) -> tuple[str, str]:
    client = openai_client(api_key_env, base_url)

    responses_error: Exception | None = None
    if api_mode in {"auto", "responses"}:
        try:
            return describe_with_responses(client, item, image_path, model), "responses"
        except Exception as exc:
            if api_mode == "responses":
                raise
            responses_error = exc

    try:
        return describe_with_chat(client, item, image_path, model), "chat"
    except Exception as chat_error:
        if responses_error is not None:
            raise RuntimeError(f"Responses failed: {responses_error}; Chat Completions failed: {chat_error}") from chat_error
        raise


def is_provider_blocking_error(exc: Exception) -> bool:
    text = str(exc)
    markers = [
        "AllocationQuota.FreeTierOnly",
        "free quota has been exhausted",
        "insufficient_quota",
        "invalid_api_key",
        "Incorrect API key",
        "Unauthorized",
        "PermissionDenied",
    ]
    return any(marker.lower() in text.lower() for marker in markers)


def replacement_for(item: VisualItem, description: str) -> str:
    return (
        f"**Visual element {item.visual_id}: {item.label}**\n\n"
        f"- Asset: `{item.asset}`\n"
        "- Textual description:\n\n"
        f"{clean_model_text(description)}\n\n"
        "- Uncertainty: see description above.\n"
    )


def skipped_replacement_for(item: VisualItem, reason: str) -> str:
    return (
        f"**Visual element {item.visual_id}: {item.label}**\n\n"
        f"- Asset: `{item.asset}`\n"
        f"- Textual description: skipped as likely decorative or low-information visual asset ({reason}).\n"
        "- Uncertainty: verify manually if this element is semantically important.\n"
    )


def write_jsonl(path: Path, rows: Iterable[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def append_visual_audit(
    path: Path,
    provider: str,
    model: str,
    api_mode: str,
    api_key_env: str,
    base_url: str | None,
    rows: list[dict[str, object]],
) -> None:
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status", "unknown"))
        counts[status] = counts.get(status, 0) + 1
    lines = [
        "# Visual Description Audit",
        "",
        f"- Created at: {datetime.now(timezone.utc).isoformat()}",
        f"- Provider: {provider}",
        f"- Model: {model}",
        f"- API mode: {api_mode}",
        f"- API key env: {api_key_env}",
        f"- Base URL configured: {'yes' if base_url else 'no'}",
        f"- Total visual items: {len(rows)}",
    ]
    for status, count in sorted(counts.items()):
        lines.append(f"- {status}: {count}")
    lines.extend(["", "## Notes", ""])
    if provider == "dry-run":
        lines.append("- Dry run only: no visual asset was sent to an external model.")
    else:
        lines.append("- External visual model was used; verify confidentiality requirements.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def update_structure(
    package_dir: Path,
    output_content: Path,
    descriptions_path: Path,
    audit_path: Path,
    rows: list[dict[str, object]],
) -> None:
    structure_path = package_dir / "structure.json"
    if not structure_path.exists():
        return
    try:
        data = json.loads(structure_path.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(data, dict):
        return
    artifacts = data.setdefault("artifacts", {})
    if isinstance(artifacts, dict):
        artifacts["content_visual_md"] = str(output_content.relative_to(package_dir))
        artifacts["visual_descriptions_jsonl"] = str(descriptions_path.relative_to(package_dir))
        artifacts["visual_audit_md"] = str(audit_path.relative_to(package_dir))
    data["visual_description"] = {
        "total": len(rows),
        "status_counts": {},
        "content_visual_md": str(output_content.relative_to(package_dir)),
        "visual_descriptions_jsonl": str(descriptions_path.relative_to(package_dir)),
        "visual_audit_md": str(audit_path.relative_to(package_dir)),
    }
    counts = data["visual_description"]["status_counts"]
    for row in rows:
        status = str(row.get("status", "unknown"))
        counts[status] = counts.get(status, 0) + 1
    structure_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    args = parse_args()
    package_dir = Path(args.package_dir).resolve()
    content_path = package_dir / "content.md"
    if not content_path.exists():
        raise SystemExit(f"content.md not found: {content_path}")

    content = content_path.read_text(encoding="utf-8", errors="replace")
    items = extract_visual_items(package_dir, content)
    if args.limit is not None:
        items = items[: args.limit]

    provider = args.provider
    if provider == "auto":
        provider = "dry-run"

    if provider == "openai" and not args.allow_external:
        raise SystemExit("Refusing external API call: pass --allow-external to send assets to the vision API.")
    env_name = args.api_key_env
    if provider == "openai" and not os.getenv(env_name):
        raise SystemExit(f"{env_name} is not set.")

    replacements: dict[int, str] = {}
    rows: list[dict[str, object]] = []

    def report_progress(index: int, item: VisualItem, status: str) -> None:
        print(f"[{index}/{len(items)}] visual {item.visual_id}: {status}", flush=True)

    for index, item in enumerate(items, start=1):
        asset_path = (package_dir / item.asset).resolve()
        if not asset_path.exists():
            rows.append(item_to_json(item, package_dir, "missing_asset", error="asset file does not exist"))
            report_progress(index, item, "missing_asset")
            continue
        if asset_path.stat().st_size < args.min_size_bytes:
            replacements[item.visual_id] = skipped_replacement_for(
                item,
                f"{asset_path.stat().st_size} bytes < min-size-bytes {args.min_size_bytes}",
            )
            rows.append(item_to_json(item, package_dir, "skipped_small_asset"))
            report_progress(index, item, "skipped_small_asset")
            continue
        if provider == "dry-run":
            rows.append(item_to_json(item, package_dir, "pending"))
            report_progress(index, item, "pending")
            continue
        try:
            description = describe_with_openai(
                item,
                asset_path,
                args.model,
                args.api_key_env,
                args.base_url,
                args.api_mode,
            )
            description_text, api_mode_used = description
            description_text = clean_model_text(description_text)
            replacements[item.visual_id] = replacement_for(item, description_text)
            rows.append(
                item_to_json(
                    item,
                    package_dir,
                    "described",
                    description=description_text,
                    api_mode=api_mode_used,
                )
            )
            report_progress(index, item, f"described via {api_mode_used}")
        except Exception as exc:
            rows.append(item_to_json(item, package_dir, "error", error=str(exc), api_mode=args.api_mode))
            report_progress(index, item, "error")
            if provider == "openai" and not args.continue_on_api_error and is_provider_blocking_error(exc):
                break

    def replace_block(match: re.Match[str]) -> str:
        visual_id = int(match.group("id"))
        return replacements.get(visual_id, match.group(0))

    output_content = VISUAL_BLOCK_RE.sub(replace_block, content)
    output_path = content_path if args.in_place else package_dir / args.output_content
    output_path.write_text(output_content, encoding="utf-8")

    descriptions_path = package_dir / args.descriptions_output
    audit_path = package_dir / args.audit_output
    write_jsonl(descriptions_path, rows)
    append_visual_audit(audit_path, provider, args.model, args.api_mode, args.api_key_env, args.base_url, rows)
    update_structure(package_dir, output_path, descriptions_path, audit_path, rows)

    print(f"Wrote content: {output_path}")
    print(f"Wrote descriptions: {descriptions_path}")
    print(f"Wrote visual audit: {audit_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
