#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import html
import json
import re
from pathlib import Path


IGNORED_TYPES = {"header", "footer", "page_number"}
VISUAL_TYPES = {"chart", "image"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Route MinerU pages to keep, augment, or rewrite using a local risk score."
    )
    parser.add_argument("package_dir", help="Converted document package directory.")
    parser.add_argument(
        "--sensitivity",
        type=int,
        required=True,
        help="Semantic refinement sensitivity from 0 to 100. Higher values select more pages.",
    )
    parser.add_argument(
        "--output",
        default="semantic_selection.json",
        help="Selection JSON filename inside the package directory.",
    )
    return parser.parse_args()


def source_path(package_dir: Path) -> Path:
    structure_path = package_dir / "structure.json"
    if not structure_path.exists():
        raise SystemExit(f"structure.json not found: {structure_path}")
    structure = json.loads(structure_path.read_text(encoding="utf-8"))
    source = Path(str(structure.get("source") or ""))
    if not source.exists():
        raise SystemExit(f"source PDF not found: {source}")
    return source.resolve()


def mineru_rows(package_dir: Path) -> list[dict[str, object]]:
    candidates = list(
        (package_dir / "engine_output").glob("**/__research_doc_ingest_merged_content_list.json")
    )
    if not candidates:
        raise SystemExit("merged MinerU content list not found; run MinerU with --keep-raw")
    return json.loads(candidates[0].read_text(encoding="utf-8"))


def plain_text(value: object) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def row_evidence(rows: list[dict[str, object]]) -> str:
    pieces: list[str] = []
    for row in rows:
        if str(row.get("type")) in IGNORED_TYPES:
            continue
        for key in (
            "text",
            "table_body",
            "table_caption",
            "image_caption",
            "chart_caption",
        ):
            value = plain_text(row.get(key))
            if value:
                pieces.append(value)
    return " ".join(pieces)


def text_only_evidence(rows: list[dict[str, object]]) -> str:
    return " ".join(
        plain_text(row.get("text"))
        for row in rows
        if row.get("type") == "text" and plain_text(row.get("text"))
    )


def visual_area_ratio(rows: list[dict[str, object]]) -> float:
    area = 0.0
    for row in rows:
        if row.get("type") not in VISUAL_TYPES:
            continue
        bbox = row.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            continue
        x0, y0, x1, y1 = (float(value) for value in bbox)
        area += max(0.0, x1 - x0) * max(0.0, y1 - y0) / 1_000_000
    return min(1.0, area)


def nonempty_caption_count(rows: list[dict[str, object]]) -> int:
    count = 0
    for row in rows:
        if row.get("type") not in VISUAL_TYPES:
            continue
        if plain_text(row.get("image_caption")) or plain_text(row.get("chart_caption")):
            count += 1
    return count


def score_page(
    rows: list[dict[str, object]],
    pdf_text: str,
) -> tuple[int, list[str], dict[str, object]]:
    counts = Counter(str(row.get("type")) for row in rows)
    meaningful = [row for row in rows if str(row.get("type")) not in IGNORED_TYPES]
    evidence = row_evidence(rows)
    text_evidence = text_only_evidence(rows)
    evidence_chars = len(re.sub(r"\s+", "", evidence))
    pdf_chars = len(re.sub(r"\s+", "", pdf_text))
    coverage = min(1.0, evidence_chars / max(1, pdf_chars))
    area_ratio = visual_area_ratio(rows)
    caption_count = nonempty_caption_count(rows)
    chart_count = counts["chart"]
    image_count = counts["image"]
    visual_count = chart_count + image_count
    short_text_blocks = sum(
        1
        for row in meaningful
        if row.get("type") == "text" and 0 < len(plain_text(row.get("text"))) <= 30
    )
    numeric_count = len(re.findall(r"\d+(?:[.,]\d+)?%?", evidence))

    score = 0
    reasons: list[str] = []

    if chart_count:
        points = min(55, 40 + 8 * (chart_count - 1))
        score += points
        reasons.append(f"chart_blocks:{chart_count}")
    if image_count:
        points = min(35, 20 + 5 * (image_count - 1))
        score += points
        reasons.append(f"image_blocks:{image_count}")

    if area_ratio >= 0.45:
        score += 30
        reasons.append(f"visual_area:{area_ratio:.2f}")
    elif area_ratio >= 0.25:
        score += 20
        reasons.append(f"visual_area:{area_ratio:.2f}")
    elif area_ratio >= 0.10:
        score += 10
        reasons.append(f"visual_area:{area_ratio:.2f}")

    if caption_count:
        score += 10
        reasons.append(f"captioned_visuals:{caption_count}")

    if image_count >= 2 and area_ratio >= 0.10:
        score += 15
        reasons.append("multi_panel_visual")

    if coverage < 0.45:
        score += 20
        reasons.append(f"text_coverage:{coverage:.2f}")
    elif coverage < 0.75:
        score += 10
        reasons.append(f"text_coverage:{coverage:.2f}")

    if visual_count and len(text_evidence) < 250:
        score += 10
        reasons.append(f"low_text:{len(text_evidence)}")

    if len(meaningful) >= 16:
        score += 10
        reasons.append(f"layout_blocks:{len(meaningful)}")
    elif len(meaningful) >= 10:
        score += 5
        reasons.append(f"layout_blocks:{len(meaningful)}")

    if short_text_blocks >= 6 and numeric_count >= 5:
        score += 10
        reasons.append("kpi_or_callout_layout")

    if counts["table"] and visual_count:
        score += 10
        reasons.append("mixed_table_visual")

    features: dict[str, object] = {
        "types": dict(sorted(counts.items())),
        "mineru_evidence_chars": evidence_chars,
        "mineru_text_chars": len(text_evidence),
        "pdf_text_chars": pdf_chars,
        "text_coverage": round(coverage, 3),
        "visual_area_ratio": round(area_ratio, 3),
        "captioned_visuals": caption_count,
        "short_text_blocks": short_text_blocks,
        "numeric_tokens": numeric_count,
    }
    return min(100, score), reasons, features


def action_for_page(
    score: int,
    threshold: int,
    features: dict[str, object],
) -> str:
    types = dict(features["types"])
    chart_count = int(types.get("chart", 0))
    image_count = int(types.get("image", 0))
    table_count = int(types.get("table", 0))
    text_chars = int(features["mineru_text_chars"])
    coverage = float(features["text_coverage"])
    area_ratio = float(features["visual_area_ratio"])
    caption_count = int(features["captioned_visuals"])

    hard_failure = (image_count > 0 and text_chars == 0 and table_count == 0) or (
        coverage < 0.20 and image_count > 0 and table_count == 0
    )
    decorative_small_images = (
        threshold > 0
        and chart_count == 0
        and image_count > 0
        and area_ratio < 0.10
        and caption_count == 0
        and coverage >= 0.20
    )
    if decorative_small_images:
        return "keep"
    if score < threshold and not hard_failure:
        return "keep"
    if table_count or chart_count:
        return "augment"
    if hard_failure or (image_count >= 3 and text_chars < 300) or text_chars < 120:
        return "rewrite"
    return "augment"


def page_spec(pages: list[int]) -> str:
    if not pages:
        return ""
    ranges: list[str] = []
    start = previous = pages[0]
    for page in pages[1:]:
        if page == previous + 1:
            previous = page
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = page
    ranges.append(str(start) if start == previous else f"{start}-{previous}")
    return ",".join(ranges)


def main() -> int:
    args = parse_args()
    if not 0 <= args.sensitivity <= 100:
        raise SystemExit("--sensitivity must be between 0 and 100")

    import fitz  # type: ignore

    package_dir = Path(args.package_dir).resolve()
    source = source_path(package_dir)
    rows = mineru_rows(package_dir)
    rows_by_page: dict[int, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        rows_by_page[int(row.get("page_idx", 0)) + 1].append(row)

    threshold = 100 - args.sensitivity
    page_rows: list[dict[str, object]] = []
    with fitz.open(str(source)) as doc:
        for page in range(1, doc.page_count + 1):
            score, reasons, features = score_page(
                rows_by_page.get(page, []),
                doc.load_page(page - 1).get_text("text"),
            )
            action = action_for_page(score, threshold, features)
            page_rows.append(
                {
                    "page": page,
                    "score": score,
                    "action": action,
                    "reasons": reasons,
                    "features": features,
                }
            )

    selected = [int(row["page"]) for row in page_rows if row["action"] != "keep"]
    counts = Counter(str(row["action"]) for row in page_rows)
    payload = {
        "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "package_dir": str(package_dir),
        "source": str(source),
        "sensitivity": args.sensitivity,
        "risk_threshold": threshold,
        "expected_pages": len(page_rows),
        "selected_page_spec": page_spec(selected),
        "summary": dict(sorted(counts.items())),
        "pages": page_rows,
    }
    output_path = package_dir / args.output
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote semantic selection: {output_path}")
    print(f"Sensitivity: {args.sensitivity}; risk threshold: {threshold}")
    print(
        "Routes: "
        + ", ".join(f"{name}={counts.get(name, 0)}" for name in ("keep", "augment", "rewrite"))
    )
    print(f"Selected pages: {page_spec(selected) or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
