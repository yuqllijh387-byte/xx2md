# Research Doc Ingest Skill

Codex skill for converting research PDFs and presentations into page-complete Markdown with a MinerU baseline and selectively routed Qwen page semantics.

## Install

Give Codex the direct GitHub directory URL:

```text
https://github.com/<owner>/<repo>/tree/main/research-doc-ingest
```

Ask it to install the skill from that URL, then restart Codex. On first use, the skill instructs the agent to create its pinned Python runtime, check MinerU, and configure `DASHSCOPE_API_KEY` locally.

For a private repository, the target computer must already have GitHub access through `GH_TOKEN`/`GITHUB_TOKEN` or Git credentials. When archive download is unavailable but SSH authentication works, install with the skill installer's Git method.

The repository contains no API keys, source documents, converted documents, model weights, or generated page images.
