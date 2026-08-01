#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
import venv


MIN_PYTHON = (3, 10)
MAX_PYTHON = (3, 14)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create an isolated runtime for research-doc-ingest."
    )
    parser.add_argument(
        "--venv",
        default=str(Path.home() / ".research-doc-ingest" / "venv"),
        help="Virtual environment path.",
    )
    parser.add_argument(
        "--upgrade",
        action="store_true",
        help="Upgrade packages that already satisfy the pinned requirements.",
    )
    return parser.parse_args()


def venv_python(venv_dir: Path) -> Path:
    if sys.platform.startswith("win"):
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def main() -> int:
    args = parse_args()
    version = sys.version_info[:2]
    if not (MIN_PYTHON <= version < MAX_PYTHON):
        raise SystemExit(
            "Python 3.10-3.13 is required; Python 3.11 is recommended. "
            f"Current interpreter: {sys.version.split()[0]}"
        )

    skill_dir = Path(__file__).resolve().parent.parent
    requirements = skill_dir / "requirements.txt"
    if not requirements.exists():
        raise SystemExit(f"requirements.txt not found: {requirements}")

    venv_dir = Path(args.venv).expanduser().resolve()
    python = venv_python(venv_dir)
    if not python.exists():
        venv_dir.parent.mkdir(parents=True, exist_ok=True)
        venv.EnvBuilder(with_pip=True).create(venv_dir)

    command = [
        str(python),
        "-m",
        "pip",
        "install",
        "--requirement",
        str(requirements),
    ]
    if args.upgrade:
        command.append("--upgrade")
    subprocess.run(command, check=True)

    checker = skill_dir / "scripts" / "check_environment.py"
    subprocess.run([str(python), str(checker)], check=True)
    print(f"Runtime Python: {python}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
