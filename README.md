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

## Install into Hermes Agent

```bash
hermes skills install yuqllijh387-byte/xx2md/research-doc-ingest --force
```

`--force` is expected: the heuristic security scan reports a caution verdict (subprocess engine launches plus one deliberate loopback `no_proxy` assignment). CRITICAL findings are kept at zero and regression-tested in `tests/test_scanner_hygiene.py`. If you prefer not to use `--force`, clone this repository, review the scripts, and copy `research-doc-ingest/` into `~/.hermes/skills/` — Hermes loads it as a local skill.

Then follow `research-doc-ingest/references/setup.md`: bootstrap the pinned Python 3.11 runtime, set `DASHSCOPE_API_KEY` in `~/.hermes/.env`, and run `check_environment.py`.

## Development

```bash
python -m pip install pytest
python -m pytest tests -q
python tools/validate_skill.py
```

The GitHub Actions `validate` workflow runs the same checks on every push.
