#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


MIN_PYTHON = (3, 10)
MAX_PYTHON = (3, 14)
REQUIRED_DISTRIBUTIONS = {
    "mineru": "3.4.4",
    "openai": "2.45.0",
    "PyMuPDF": "1.28.0",
    "pypdf": "6.14.2",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check the research-doc-ingest runtime without exposing secrets."
    )
    parser.add_argument("--json", action="store_true", help="Write machine-readable JSON.")
    return parser.parse_args()


def distribution_status(name: str, expected: str) -> dict[str, object]:
    try:
        installed = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return {
            "name": name,
            "expected": expected,
            "installed": None,
            "ok": False,
        }
    return {
        "name": name,
        "expected": expected,
        "installed": installed,
        "ok": installed == expected,
    }


def mineru_status() -> dict[str, object]:
    executable = shutil.which("mineru")
    if not executable:
        executable_name = "mineru.exe" if sys.platform.startswith("win") else "mineru"
        candidates = [
            Path(sys.executable).resolve().parent / executable_name,
            Path(sys.prefix)
            / ("Scripts" if sys.platform.startswith("win") else "bin")
            / executable_name,
        ]
        executable = next(
            (str(candidate) for candidate in candidates if candidate.exists()),
            None,
        )
    if not executable:
        return {"executable": None, "version_output": None, "ok": False}

    result = subprocess.run(
        [executable, "--version"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        errors="replace",
        timeout=60,
    )
    output = result.stdout.strip()
    return {
        "executable": executable,
        "version_output": output,
        "ok": result.returncode == 0 and "3.4.4" in output,
    }


def main() -> int:
    args = parse_args()
    version = sys.version_info[:2]
    packages = [
        distribution_status(name, expected)
        for name, expected in REQUIRED_DISTRIBUTIONS.items()
    ]
    mineru = mineru_status()
    report = {
        "python": {
            "executable": sys.executable,
            "version": sys.version.split()[0],
            "supported": MIN_PYTHON <= version < MAX_PYTHON,
            "recommended": version == (3, 11),
        },
        "packages": packages,
        "mineru_cli": mineru,
        "dashscope_api_key_configured": bool(os.environ.get("DASHSCOPE_API_KEY")),
        "ready_for_local_conversion": all(item["ok"] for item in packages)
        and bool(mineru["ok"]),
        "ready_for_qwen_semantics": bool(os.environ.get("DASHSCOPE_API_KEY")),
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"Python: {report['python']['version']} ({sys.executable})")
        for item in packages:
            status = "OK" if item["ok"] else "MISMATCH"
            print(
                f"{status}: {item['name']} "
                f"installed={item['installed']} expected={item['expected']}"
            )
        print(
            f"{'OK' if mineru['ok'] else 'MISSING'}: MinerU CLI "
            f"{mineru['version_output'] or ''}".rstrip()
        )
        print(
            "DASHSCOPE_API_KEY: "
            + ("configured" if report["dashscope_api_key_configured"] else "not configured")
        )

    return 0 if report["ready_for_local_conversion"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
