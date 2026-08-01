# Engine Selection

Use this reference when the conversion quality matters or the first engine produces weak output.

## Default Choices

| Source | Preferred engine | Fallback | Notes |
| --- | --- | --- | --- |
| Research PDF | MinerU | Docling | Best for papers, formulas, tables, multi-column layout, OCR. |
| Scanned PDF | MinerU | Docling with OCR | Require page-level audit and uncertainty flags. |
| PPT/PPTX | MinerU | Docling; LibreOffice-to-PDF then MinerU | Preserve slide boundaries and describe visual layouts. |
| DOC/DOCX | Docling | MinerU | Usually text structure matters more than visual layout. |
| XLS/XLSX | Docling | MinerU | Preserve tables; avoid flattening complex sheets without warnings. |
| Images | MinerU/OCR pipeline | Vision model | Use visual descriptions for charts, diagrams, and screenshots. |
| Quick rough conversion | MarkItDown | Docling | Use only when completeness is not critical. |
| Smoke test without installed engines | Built-in pypdf fallback | Install MinerU/Docling | PDF text only; no OCR, layout, figures, tables, or visual understanding. |

## Quality Modes

`research-archive`:

- Use for thesis/literature ingestion.
- Produce `content.md`, `chunks.jsonl`, `structure.json`, `audit.md`, and `assets/`.
- Preserve page/slide boundaries.
- Extract assets and describe key visuals.

`rag-lite`:

- Use for quick searchable text ingestion.
- Produce `content.md` and `chunks.jsonl`.
- Warn that visual details may be incomplete.

`single-md`:

- Use only when the user explicitly wants one file.
- Do not embed images by default.
- Replace visual assets with textual descriptions and uncertainty notes.

## Visual Policy

`describe`:

- Describe key visuals when the engine provides captions, OCR, or image descriptions.
- Keep uncertain items in `audit.md`.

`strict`:

- Fail or mark conversion incomplete when key visuals cannot be described.
- Use this for important papers, thesis sources, and slides with dense figures.

`off`:

- Extract text only.
- Use only when the user says visual information is irrelevant.

## Baseline and Visual Description Pass

Baseline:

- After MinerU or Docling produces an accepted package, run `scripts/mark_baseline.py`.
- Treat `baseline.json` as the reproducibility checkpoint before visual post-processing.
- Do not overwrite the baseline package silently; write later variants as `content.visual.md` or a new output directory.

Visual description:

- Run `scripts/describe_visuals.py <package> --provider dry-run` first.
- This creates `visual_descriptions.jsonl` as the queue of visual elements that need textual descriptions.
- Use `--provider openai --allow-external` only if the user explicitly permits sending the visual assets to OpenAI.
- Keep `content.md` unchanged by default; write descriptions to `content.visual.md`.
- Use `--in-place` only after reviewing output quality.

## Engine Expectations

MinerU:

- Use for completeness-sensitive PDF/PPT ingestion.
- Resolve the backend explicitly. Use `pipeline` on pure CPU systems; use local `hybrid-engine` only when supported GPU/MPS acceleration is available.
- Do not omit `-b` and inherit MinerU's version-dependent default backend.
- Use recoverable page batches for long PDFs and resume from the batch manifest after interruption.
- Stream progress, emit heartbeats, enforce timeouts, and terminate the full local API process tree on cancellation.
- Prefer modes that output Markdown and structured data.
- Enable OCR automatically for scanned or low-text pages.
- Enable formula and table extraction when available.
- For slide-style PDFs with a usable text layer, compare MinerU against PyMuPDF plus page-level vision; prefer the latter when visual semantics matter more than document-parser structure.

Docling:

- Use as the stable fallback or primary engine for Office documents.
- Prefer Markdown plus JSON/chunks output when available.
- Enable picture description, chart extraction, table structure, formula enrichment, and OCR when available.

MarkItDown:

- Use only as a quick fallback.
- Good for simple text extraction.
- Not sufficient alone for research-grade visual completeness.

Built-in pypdf fallback:

- Use only to verify that packaging, chunking, and audit generation work.
- It extracts per-page PDF text only.
- It must warn that layout, OCR, figures, tables, formulas, and visual understanding are incomplete.

External vision model:

- Use when key figures, charts, diagrams, or slide layouts are present but not described by the document parser.
- Describe the image in text, including axes, labels, units, visual relations, and the scientific claim.
- Mark any unreadable or inferred details explicitly.
- Require an explicit external-call decision before sending confidential or commercial documents to an API.

## Installation Notes

These tools evolve. Before hardcoding commands, check the installed CLI help:

```powershell
mineru --help
docling --help
markitdown --help
```

If a command is not installed, install it in the active Python environment or use another configured environment. Do not install dependencies silently without user approval when working in a managed project.

## Audit Checklist

- Expected page/slide count equals converted page/slide count.
- `content.md` contains page or slide headings.
- Key visual elements have textual descriptions or explicit warnings.
- Complex tables are represented as Markdown/HTML or flagged.
- Formulas are represented as LaTeX or flagged.
- OCR was applied where needed and low-confidence regions are listed.
- `chunks.jsonl` records source and page/slide metadata.
- `structure.json` records engine, source file, generated artifacts, warnings, and raw paths when kept.
- `baseline.json` exists after accepting a package as the parsing baseline.
- `visual_descriptions.jsonl` exists after the visual pass and records pending/described/error status.
