---
name: research-doc-ingest
description: Convert research PDFs, slides, Word documents, spreadsheets, images, and web exports into AI-ready research document packages. Use when a user wants document ingestion, PDF/PPT/DOCX-to-Markdown conversion, RAG or knowledge-base preprocessing, visual figure/table understanding, OCR, auditability, or preservation of source traceability beyond a plain Markdown file.
---

# Research Doc Ingest

Use this skill to turn research documents into a package that is readable by humans, usable by AI/RAG systems, and traceable back to the source.

## First Run

On a newly installed computer, read `references/setup.md` before conversion. Use a dedicated Python 3.11 environment and run:

```powershell
python <skill>/scripts/bootstrap_environment.py
<runtime-python> <skill>/scripts/check_environment.py
```

Use `<runtime-python>` for all commands below. Do not assume the agent's default `python` has MinerU or the pinned packages.

Default package layout:

```text
<output>/<source-stem>/
  content.md
  chunks.jsonl
  structure.json
  audit.md
  assets/
```

## Core Rules

- Treat `content.md` as the main reading artifact, not as the only source of truth.
- Preserve page or slide boundaries in `content.md`.
- Do not embed images as base64 unless the user explicitly asks for single-file archival Markdown.
- Keep visual evidence in `assets/`; describe key figures, diagrams, flowcharts, charts, tables, and formula images in text inside `content.md`.
- Use `chunks.jsonl` for RAG ingestion and `structure.json` for machine traceability.
- Write `audit.md` for completeness risks: missing pages, OCR pages, low-confidence visual reading, failed tables, failed formulas, or engine fallbacks.
- Never silently drop visual or tabular information. If a figure/table cannot be read confidently, mark it in `content.md` and `audit.md`.

## Quick Start

Prefer the bundled wrapper:

```powershell
<runtime-python> <skill>/scripts/convert_research_doc.py "input.pdf" --output-dir "converted" --keep-raw
<runtime-python> <skill>/scripts/mark_baseline.py "converted/input"
<runtime-python> <skill>/scripts/select_semantic_pages.py "converted/input" --sensitivity 50
<runtime-python> <skill>/scripts/describe_pages.py "converted/input" --selection semantic_selection.json --provider openai --allow-external --api-key-env DASHSCOPE_API_KEY --base-url "https://dashscope.aliyuncs.com/compatible-mode/v1" --models qwen3.7-plus --prompt-mode markdown-only --omit-boilerplate --render-dpi 120 --concurrency 3 --output-prefix page_semantics_selected
<runtime-python> <skill>/scripts/build_routed_markdown.py "converted/input" --selection semantic_selection.json --page-semantics page_semantics_selected.jsonl --output-content input.md
```

Useful options:

```powershell
python <skill>/scripts/convert_research_doc.py "slides.pptx" --engine auto --visual-policy describe --output-dir "converted"
python <skill>/scripts/convert_research_doc.py "paper.pdf" --engine mineru --ocr auto --mineru-backend auto --mineru-batch-size 20 --keep-raw
python <skill>/scripts/convert_research_doc.py "paper.pdf" --engine mineru --ocr auto --mineru-batch-size 20 --resume --keep-raw
python <skill>/scripts/convert_research_doc.py "paper.pdf" --engine docling --visual-policy strict
python <skill>/scripts/select_semantic_pages.py "converted/paper" --sensitivity 30
python <skill>/scripts/select_semantic_pages.py "converted/paper" --sensitivity 70
```

The wrapper tries engines in this order when `--engine auto` is used:

1. MinerU
2. Docling
3. MarkItDown
4. Built-in `PyMuPDF` PDF fallback with page text and page snapshots
5. Built-in `pypdf` text-only fallback for PDFs

Use MarkItDown only for quick rough conversion. It is not the default for completeness-sensitive work.
Use the built-in PDF fallbacks only as smoke tests when no real engine is installed.

MinerU safety defaults:

- Resolve `--mineru-backend auto` to `pipeline` when CUDA or Apple MPS acceleration is unavailable; never rely on MinerU's version-dependent default backend.
- Process long PDFs in recoverable 20-page batches by default. Set `--mineru-batch-size 0` only when one-shot processing is intentional.
- Show MinerU output and periodic heartbeats while it runs.
- Preserve `.engine_work` after a failed or interrupted batched run. Re-run the same source and output directory with `--resume` to reuse completed batches.
- Apply a per-batch hard timeout and no-output timeout, and terminate the full MinerU process tree on timeout or interruption.

## Workflow

1. Ask for semantic sensitivity before starting unless the user already supplied it.
2. Identify the source type and choose the engine using `references/engine-selection.md`.
3. Run `scripts/convert_research_doc.py`.
4. Run `scripts/mark_baseline.py` once the MinerU/Docling package is accepted as the parsing baseline.
5. Run `scripts/select_semantic_pages.py` locally. It assigns every page to `keep`, `augment`, or `rewrite` without an API call.
6. Run `scripts/describe_pages.py --selection semantic_selection.json` with `qwen3.7-plus`. Only `augment` and `rewrite` pages are sent.
7. Run `scripts/build_routed_markdown.py`. `keep` pages use cleaned MinerU output; `augment` pages retain MinerU prose/numbers/tables and append missing visual semantics; `rewrite` pages use full-page semantic Markdown.
8. Inspect the routed audit and spot-check high-score pages against rendered source pages. Do not add a second-model review stage.

## Required Sensitivity Choice

Before executing a conversion/refinement task, ask the user to choose one value when it is not already specified:

- `0`: only MinerU hard failures.
- `30`: conservative; obvious complex or failed pages.
- `50`: balanced and recommended; key charts, diagrams, and complex layouts.
- `70`: thorough; more visual/layout pages and higher API usage.
- `100`: all pages; equivalent to full page-semantic processing.

Use the single CLI parameter `--sensitivity`. Higher values select more pages. Do not start external page-semantic calls before the choice is known.

## Page-Level Model Strategy

- Use MinerU as the full-document parsing baseline and `qwen3.7-plus` only for locally selected pages.
- Do not run a `qwen3-vl-plus` review stage.
- A dense table alone is not a rewrite trigger. Preserve MinerU tables and exact numbers, then use `augment` only when a chart, diagram, or layout relationship adds missing meaning.
- Use `rewrite` only when image-dominant layout, low extraction coverage, KPI cards, or reading-order failure makes MinerU Markdown semantically inadequate.
- Do not treat MinerU heading levels as final semantics for slide decks. Large numbers can be KPI card values rather than headings.
- Use `--prompt-mode markdown-only --omit-boilerplate --render-dpi 120 --concurrency 2` or `--concurrency 3` for selected pages.
- Omit headers, footers, page numbers, logos, confidentiality labels, and watermarks from final Markdown unless they contain substantive document content.

## Visual Understanding Standard

For every important visual element, `content.md` should include:

- Type: plot, diagram, flowchart, microscopy image, spectrum, table, formula image, architecture figure, or slide layout.
- Local source: page or slide number and any caption/title.
- Literal content: labels, axes, units, legends, nodes, arrows, table headers, and visible text when readable.
- Interpretation: the trend, comparison, mechanism, or claim supported by the visual.
- Uncertainty: any unreadable labels, dense regions, cropped content, or likely OCR/vision errors.

Do not invent values, labels, or relationships that are not visible or inferable from the source.

External visual APIs:

- Do not send document images to an external provider unless the user explicitly allows it.
- Use `--provider dry-run` to generate `visual_descriptions.jsonl` as a review queue without external calls.
- Use `--provider openai --allow-external` only when confidentiality requirements permit sending assets to an external vision provider.
- Use `--base-url`, `--api-key-env`, and usually `--api-mode chat` for OpenAI-compatible providers that expose Chat Completions.
- Never paste API keys into chat or commit them to the skill folder; set them as local environment variables.

## Outputs

`content.md`:

- Human and AI readable main artifact.
- Contains page/slide headings, extracted text, formulas, tables, and textual descriptions of key visuals.

`chunks.jsonl`:

- One JSON object per chunk.
- Include at least `text`, `source`, `page_or_slide`, `type`, and `chunk_index` when available.

`chunks.integrated.jsonl`:

- Optional RAG chunk file generated from `content.integrated.md`.
- Use this instead of `chunks.jsonl` when page-level semantic integration is accepted as the primary reading artifact.

`structure.json`:

- Machine traceability layer.
- Record source metadata, selected engine, page/slide counts, generated files, warnings, and raw engine output paths if retained.

`audit.md`:

- Completeness and reliability report.
- List expected vs converted pages/slides, visual description counts, OCR use, fallback engines, missing assets, and warnings.

`baseline.json`:

- Deterministic snapshot of accepted package artifacts.
- Records hashes and counts for `content.md`, `chunks.jsonl`, `structure.json`, `audit.md`, assets, and raw output.

`content.visual.md` and `visual_descriptions.jsonl`:

- Optional visual-understanding layer generated by `scripts/describe_visuals.py`.
- `content.visual.md` keeps the original `content.md` intact unless `--in-place` is used.
- `visual_descriptions.jsonl` records described, pending, skipped, or failed visual elements.

`page_semantics*.md` and `page_semantics*.jsonl`:

- Optional page-level semantic layer generated by `scripts/describe_pages.py`.
- Uses full rendered page images plus MinerU page text to distinguish true headings from KPI cards, callouts, captions, decorative symbols, and page-level arguments.
- Use this layer before producing a final integrated Markdown when slide layout or visual-text relationships are important.

`content.integrated.md`:

- Final AI/knowledge-base reading artifact after accepted page-level semantics are merged.
- Should preserve important values and traceability while correcting layout-derived mistakes such as KPI values incorrectly promoted to headings.
- For samples, use `content.integrated.sample.md` and keep formal `content.integrated.md` for accepted full-document output.

`assets/`:

- Page snapshots, slide snapshots, extracted figures, and complex table artifacts.
- These are evidence files for checking, not the primary AI ingestion text.

## Resources

- Run `scripts/convert_research_doc.py` for conversion packaging.
- Run `scripts/mark_baseline.py` to freeze a baseline package before visual post-processing.
- Run `scripts/describe_visuals.py` to create visual description queues or write generated descriptions.
- Run `scripts/describe_pages.py` for full-page multimodal understanding and model A/B tests on selected pages.
- Run `scripts/integrate_pages.py` to merge accepted page-level semantic Markdown into sample or final integrated Markdown.
- Run `scripts/build_recommended_markdown.py` to assemble one clean, page-complete Markdown from primary and recovery page-semantic outputs.
- Run `scripts/build_chunks.py` to rebuild page-level chunks from the accepted Markdown artifact.
- Run `scripts/bootstrap_environment.py` once on a new computer to create the pinned runtime.
- Run `scripts/check_environment.py` before first use and after dependency changes.
- Read `references/setup.md` for installation, Python, MinerU, and DashScope configuration.
- Read `references/engine-selection.md` when choosing engines, installing dependencies, or diagnosing quality issues.
