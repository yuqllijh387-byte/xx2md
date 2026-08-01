# Runtime Setup

Read this file when the skill is newly installed, when `mineru` is unavailable, or when a conversion works on one computer but not another.

## Supported Runtime

- Use Python 3.10-3.13. Prefer Python 3.11.
- The pinned and tested stack is in `requirements.txt`.
- Keep the runtime outside the skill directory so reinstalling the skill does not remove the environment.
- Expect MinerU to download model files during first use. Keep network access and sufficient disk space available.

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

## DashScope

Set `DASHSCOPE_API_KEY` in the local user environment. Never write it into the repository, command history, logs, or output package.

For the China (Beijing) default workspace, use:

```text
https://dashscope.aliyuncs.com/compatible-mode/v1
```

Workspace-specific and international endpoints differ. Use the endpoint belonging to the same region and workspace as the API key.

The selected semantic model is `qwen3.7-plus`. External page calls require both explicit user permission and `--allow-external`.

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
