#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import re
import sys

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO_ROOT / "research-doc-ingest"
SKILL_MD = SKILL_DIR / "SKILL.md"
FORBIDDEN_NAMES = {"__pycache__", ".venv", "venv", "engine_output", ".engine_work"}
FORBIDDEN_SUFFIXES = {
    ".doc",
    ".docx",
    ".jpeg",
    ".jpg",
    ".jsonl",
    ".pdf",
    ".pem",
    ".png",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
}
SECRET_PATTERNS = {
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "GitHub token": re.compile(
        r"\b(?:github_pat_[A-Za-z0-9_]{16,}|gh[pousr]_[A-Za-z0-9]{16,})\b"
    ),
    "OpenAI-compatible API key": re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    "AWS access key": re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    "Google API key": re.compile(r"\bAIza[A-Za-z0-9_-]{20,}\b"),
    "Slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    "credential-bearing URL": re.compile(r"https?://[^/@\s]+:[^/@\s]+@"),
}
MAC_USERS_ROOT = "/" + "Users/"
LINUX_HOME_ROOT = "/" + "home/"
ABSOLUTE_USER_PATH = re.compile(
    r"[A-Za-z]:\\Users\\[^\\/\s]+"
    + "|"
    + re.escape(MAC_USERS_ROOT)
    + r"[^/\s]+"
    + "|"
    + re.escape(LINUX_HOME_ROOT)
    + r"[^/\s]+"
)


def fail(message: str) -> None:
    raise SystemExit(message)


def main() -> int:
    text = SKILL_MD.read_text(encoding="utf-8")
    match = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n", text, re.S)
    if not match:
        fail("SKILL.md is missing YAML frontmatter")

    metadata = yaml.safe_load(match.group(1))
    if not isinstance(metadata, dict):
        fail("SKILL.md frontmatter must be a mapping")
    if set(metadata) != {"name", "description"}:
        fail("SKILL.md frontmatter must contain only name and description")
    if metadata["name"] != SKILL_DIR.name:
        fail("skill folder and frontmatter name differ")
    if not str(metadata["description"]).strip():
        fail("skill description is empty")

    required = [
        SKILL_DIR / "agents" / "openai.yaml",
        SKILL_DIR / "requirements.txt",
        SKILL_DIR / "references" / "setup.md",
        SKILL_DIR / "scripts" / "bootstrap_environment.py",
        SKILL_DIR / "scripts" / "check_environment.py",
    ]
    missing = [str(path.relative_to(REPO_ROOT)) for path in required if not path.is_file()]
    if missing:
        fail("missing required files: " + ", ".join(missing))

    bad_paths = [
        str(path.relative_to(REPO_ROOT))
        for path in REPO_ROOT.rglob("*")
        if ".git" not in path.parts
        and (path.name in FORBIDDEN_NAMES or path.suffix.lower() in FORBIDDEN_SUFFIXES)
    ]
    if bad_paths:
        fail("generated or source artifacts found: " + ", ".join(bad_paths))

    for path in REPO_ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        content = path.read_text(encoding="utf-8", errors="ignore")
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(content):
                fail(f"possible {label} found in {path.relative_to(REPO_ROOT)}")
        if ABSOLUTE_USER_PATH.search(content):
            fail(f"absolute user path found in {path.relative_to(REPO_ROOT)}")

    print("research-doc-ingest repository validation passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
