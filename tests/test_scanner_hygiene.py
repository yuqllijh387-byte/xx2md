"""Scanner-hygiene regression tests.

Hermes' skills-guard blocks community installs on any CRITICAL finding and
cannot be overridden with --force. These tests mirror its two CRITICAL
secret-read patterns so a future refactor cannot reintroduce them.

HIGH findings (e.g. the deliberate no_proxy assignment in
convert_research_doc.py) only produce a caution verdict, which --force can
override, so they are not asserted here.
"""

import re
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent / "research-doc-ingest"

# Mirrors of skills-guard CRITICAL patterns (case-insensitive).
CRITICAL_PATTERNS = [
    re.compile(
        r'os\.environ\s*\.get\s*\(\s*["\'][^"\']*'
        r"(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)",
        re.IGNORECASE,
    ),
    re.compile(
        r"os\.getenv\s*\(\s*[^\)]*"
        r"(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)",
        re.IGNORECASE,
    ),
]

SCANNED_SUFFIXES = {".py", ".md", ".txt", ".yaml", ".yml", ".json"}


def _skill_files():
    return [
        path
        for path in SKILL_ROOT.rglob("*")
        if path.is_file() and path.suffix in SCANNED_SUFFIXES
    ]


def test_no_scanner_critical_patterns():
    offenders = []
    for path in _skill_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in CRITICAL_PATTERNS:
            for lineno, line in enumerate(text.splitlines(), start=1):
                if pattern.search(line):
                    offenders.append(f"{path.relative_to(SKILL_ROOT)}:{lineno}: {line.strip()}")
    assert not offenders, "scanner CRITICAL patterns found:\n" + "\n".join(offenders)


def test_skill_files_discovered():
    # Guard against the test silently passing because the glob broke.
    assert any(p.name == "describe_pages.py" for p in _skill_files())
    assert any(p.name == "check_environment.py" for p in _skill_files())
