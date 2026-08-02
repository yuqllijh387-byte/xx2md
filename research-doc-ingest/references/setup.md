# Runtime Setup

Read this file when the skill is newly installed, when `mineru` is unavailable, or when a conversion works on one computer but not another.

## Supported Runtime

- Use Python 3.10-3.13. Prefer Python 3.11.
- The pinned and tested stack is in `requirements.txt`. It installs `mineru[pipeline]`, which pulls the pipeline backend dependencies (torch, torchvision, transformers `<5.0.0`, accelerate, pyclipper, shapely, and friends). Installing bare `mineru` without the `[pipeline]` extra is the most common cause of `ModuleNotFoundError: No module named 'torch'` / `'transformers'` / `'pyclipper'` at conversion time.
- `transformers` must stay `<5.0.0` for compatibility with the pinned MinerU pipeline dependency stack.
- Keep the runtime outside the skill directory so reinstalling the skill does not remove the environment.
- Expect MinerU to download several GB of model files during the first conversion. Keep network access and sufficient disk space available.

## Bootstrap

Run the bootstrap script with a supported Python interpreter:

```powershell
python <skill>/scripts/bootstrap_environment.py
```

The default environment is:

- Windows: `%USERPROFILE%\.research-doc-ingest\venv\Scripts\python.exe`
- Linux/macOS: `~/.research-doc-ingest/venv/bin/python`

Use that interpreter for every bundled script. Run the checker after installation or troubleshooting:

```powershell
<runtime-python> <skill>/scripts/check_environment.py
```

The checker reports only whether `DASHSCOPE_API_KEY` exists. It never prints the key.

If venv creation fails transiently on `ensurepip`, create the environment manually once and rerun the bootstrap (it detects the existing venv and continues with dependency installation):

```bash
python3.11 -m venv ~/.research-doc-ingest/venv
python <skill>/scripts/bootstrap_environment.py
```

## System Proxy (macOS and others)

MinerU 3.x starts a local `mineru-api` FastAPI service on `127.0.0.1` and health-checks it over HTTP. If the machine has a system-wide HTTP/HTTPS proxy enabled (common on macOS), the health check can be routed through the proxy and fail with `502 Bad Gateway` / "Timed out waiting for local mineru-api to become healthy".

`convert_research_doc.py` now adds `127.0.0.1,localhost,::1` to `no_proxy`/`NO_PROXY` automatically before launching engines, so no manual action is needed. If you run `mineru` directly outside this skill, export the same variables first.

## DashScope

Set `DASHSCOPE_API_KEY` in the local user environment. Never write it into the repository, command history, logs, or output package.

For the China (Beijing) default workspace, use:

```text
https://dashscope.aliyuncs.com/compatible-mode/v1
```

Workspace-specific and international endpoints differ. Use the endpoint belonging to the same region and workspace as the API key.

Quick key sanity check that prints only an HTTP status code (never the key):

```bash
curl -s -o /dev/null -w "%{http_code}\n" \
  -H "Authorization: Bearer $DASHSCOPE_API_KEY" \
  "https://dashscope.aliyuncs.com/compatible-mode/v1/models"
```

`200` means the key is valid; `401` means it is wrong or expired; `402`/`403` style failures around call time usually mean the account is out of balance.

The selected semantic model is `qwen3.7-plus`. External page calls require both explicit user permission and `--allow-external`.

## MinerU Backend

This skill supports only MinerU's `pipeline` backend. `--mineru-backend auto` always resolves to `pipeline`, including on systems with CUDA or Apple MPS acceleration. VLM and hybrid backends are intentionally excluded because their resource requirements and runtime stability make the skill unsuitable for typical student computers.

## Semantic Page Selection and Batching

`select_semantic_pages.py` needs the merged MinerU content list inside `engine_output/`. The converter writes it for every successful MinerU run, batched or single-shot. If you hit "merged MinerU content list not found" with a package produced by an older skill version, rerun the conversion with the same output directory (use `--keep-raw`).

For long PDFs the default 20-page batches remain the recommended, resumable mode (`--resume` reuses completed batches).

## Hermes Installation Notes

`hermes skills install` runs a heuristic security scan over the whole skill, including documentation. Community skills with any CRITICAL finding are blocked with a "dangerous" verdict that `--force` cannot override; HIGH-only findings produce a "caution" verdict that blocks the install but CAN be overridden with `--force`:

```bash
hermes skills install yuqllijh387-byte/xx2md/research-doc-ingest --force
```

The scripts read only the API-key variable you name (via cleanly-named constants/locals) and pass it to the official OpenAI SDK; they never print or transmit it elsewhere. Subprocess use is limited to documented engine CLIs (mineru, docling, markitdown) and pip. If you prefer to review before installing, clone the repository, read the scripts, and copy `research-doc-ingest/` into `~/.hermes/skills/` manually — Hermes loads it as a local skill.

## Optional Engines

MinerU is the tested primary engine. Docling and MarkItDown are optional fallbacks and are not installed by `requirements.txt`. Install them only when a task needs those engines, then verify their current CLI syntax before use.

## Installation Verification

After installing the skill from GitHub:

1. Confirm `<installed-skill>/SKILL.md` exists.
2. Run `bootstrap_environment.py`.
3. Run `check_environment.py`.
4. Run `convert_research_doc.py --help`.
5. Use a small non-confidential PDF for the first conversion.
6. Do not call DashScope until the user chooses semantic sensitivity and permits external transmission.
