#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import json
import os
import queue
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from pathlib import PurePosixPath
from typing import Iterable
from urllib.parse import unquote
from zipfile import ZipFile


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".tif", ".tiff"}
TABLE_EXTENSIONS = {".html", ".htm", ".csv", ".tsv"}


@dataclass
class EngineResult:
    engine: str
    raw_dir: Path
    command: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert a research document into an AI-ready package."
    )
    parser.add_argument("source", help="Source document path.")
    parser.add_argument("--output-dir", default="converted", help="Output root directory.")
    parser.add_argument(
        "--engine",
        choices=["auto", "mineru", "docling", "markitdown", "pymupdf", "pypdf"],
        default="auto",
        help="Conversion engine to use. Auto also has a built-in pypdf text-only fallback.",
    )
    parser.add_argument(
        "--mode",
        choices=["research-archive", "rag-lite", "single-md"],
        default="research-archive",
        help="Output package mode.",
    )
    parser.add_argument(
        "--visual-policy",
        choices=["describe", "strict", "off"],
        default="describe",
        help="How strongly to require textual handling of visual elements.",
    )
    parser.add_argument(
        "--ocr",
        choices=["auto", "on", "off"],
        default="auto",
        help="OCR preference passed to engines when supported.",
    )
    parser.add_argument(
        "--mineru-backend",
        default="auto",
        choices=[
            "auto",
            "pipeline",
            "vlm-engine",
            "hybrid-engine",
            "vlm-http-client",
            "hybrid-http-client",
        ],
        help="MinerU backend. Auto selects pipeline without local GPU/MPS acceleration.",
    )
    parser.add_argument(
        "--mineru-url",
        default=None,
        help="Optional MinerU server URL for client backends.",
    )
    parser.add_argument(
        "--mineru-timeout-seconds",
        type=int,
        default=1800,
        help="Hard timeout for one MinerU invocation. Use 0 to disable.",
    )
    parser.add_argument(
        "--mineru-stall-timeout-seconds",
        type=int,
        default=600,
        help="Stop MinerU after this many seconds without output. Use 0 to disable.",
    )
    parser.add_argument(
        "--mineru-heartbeat-seconds",
        type=int,
        default=30,
        help="Print a heartbeat while MinerU is running. Use 0 to disable.",
    )
    parser.add_argument(
        "--mineru-batch-size",
        type=int,
        default=20,
        help="Pages per recoverable MinerU PDF batch. Use 0 to disable batching.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume completed MinerU batches from the package .engine_work directory.",
    )
    parser.add_argument(
        "--docling-extra-arg",
        action="append",
        default=[],
        help="Extra argument to append to the docling command. Repeat as needed.",
    )
    parser.add_argument(
        "--keep-raw",
        action="store_true",
        help="Keep raw engine output under engine_output/.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite the output package if it already exists.",
    )
    parser.add_argument(
        "--no-rewrite-image-links",
        action="store_true",
        help="Keep image markdown links instead of replacing them with asset notes.",
    )
    return parser.parse_args()


def safe_name(name: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(" ._")
    return value or "document"


def command_path(command: str) -> str | None:
    found = shutil.which(command)
    if found:
        return found
    scripts_dir = Path(sys.prefix) / ("Scripts" if sys.platform.startswith("win") else "bin")
    suffixes = [".exe", ".bat", ".cmd", ""] if sys.platform.startswith("win") else [""]
    for suffix in suffixes:
        candidate = scripts_dir / f"{command}{suffix}"
        if candidate.exists():
            return str(candidate)
    return None


def tail(text: str, limit: int = 4000) -> str:
    text = text or ""
    return text[-limit:]


def run_command(command: list[str], cwd: Path | None = None) -> tuple[int, str, str]:
    proc = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        errors="replace",
    )
    return proc.returncode, proc.stdout, proc.stderr


def terminate_process_tree(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    if sys.platform.startswith("win"):
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run_command_streaming(
    command: list[str],
    cwd: Path | None = None,
    timeout_seconds: int = 0,
    stall_timeout_seconds: int = 0,
    heartbeat_seconds: int = 30,
) -> tuple[int, str, str]:
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform.startswith("win") else 0
    proc = subprocess.Popen(
        command,
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        errors="replace",
        bufsize=1,
        creationflags=creationflags,
        start_new_session=not sys.platform.startswith("win"),
    )
    messages: queue.Queue[tuple[str, str | None]] = queue.Queue()
    output: dict[str, list[str]] = {"stdout": [], "stderr": []}

    def drain(stream, channel: str) -> None:
        try:
            for line in iter(stream.readline, ""):
                messages.put((channel, line))
        finally:
            messages.put((channel, None))
            stream.close()

    threads = [
        threading.Thread(target=drain, args=(proc.stdout, "stdout"), daemon=True),
        threading.Thread(target=drain, args=(proc.stderr, "stderr"), daemon=True),
    ]
    for thread in threads:
        thread.start()

    started = time.monotonic()
    last_activity = started
    last_heartbeat = started
    open_streams = len(threads)
    forced_error: str | None = None
    try:
        while open_streams or proc.poll() is None:
            try:
                channel, line = messages.get(timeout=0.5)
                if line is None:
                    open_streams -= 1
                else:
                    output[channel].append(line)
                    destination = sys.stdout if channel == "stdout" else sys.stderr
                    print(line, end="", file=destination, flush=True)
                    last_activity = time.monotonic()
            except queue.Empty:
                pass

            now = time.monotonic()
            elapsed = now - started
            if heartbeat_seconds > 0 and now - last_heartbeat >= heartbeat_seconds:
                print(
                    f"[research-doc-ingest] MinerU still running: {elapsed:.0f}s elapsed",
                    file=sys.stderr,
                    flush=True,
                )
                last_heartbeat = now
            if timeout_seconds > 0 and elapsed >= timeout_seconds:
                forced_error = f"MinerU hard timeout after {timeout_seconds}s"
                terminate_process_tree(proc)
                break
            if stall_timeout_seconds > 0 and now - last_activity >= stall_timeout_seconds:
                forced_error = (
                    f"MinerU stalled: no output for {stall_timeout_seconds}s"
                )
                terminate_process_tree(proc)
                break
    except KeyboardInterrupt:
        terminate_process_tree(proc)
        raise
    finally:
        for thread in threads:
            thread.join(timeout=2)

    returncode = proc.wait(timeout=10) if proc.poll() is None else int(proc.returncode)
    if forced_error:
        output["stderr"].append(forced_error + "\n")
        print(f"[research-doc-ingest] {forced_error}", file=sys.stderr, flush=True)
        returncode = 124
    return returncode, "".join(output["stdout"]), "".join(output["stderr"])


def local_acceleration_available() -> bool:
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            return True
        mps = getattr(getattr(torch, "backends", None), "mps", None)
        return bool(mps and mps.is_available())
    except Exception:
        return False


def resolve_mineru_backend(requested: str) -> str:
    if requested != "auto":
        return requested
    return "hybrid-engine" if local_acceleration_available() else "pipeline"


def source_kind(source: Path) -> str:
    ext = source.suffix.lower()
    if ext == ".pdf":
        return "pdf"
    if ext in {".ppt", ".pptx", ".pps", ".ppsx", ".odp"}:
        return "slides"
    if ext in {".doc", ".docx", ".odt", ".rtf"}:
        return "document"
    if ext in {".xls", ".xlsx", ".ods", ".csv", ".tsv"}:
        return "spreadsheet"
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in {".html", ".htm", ".mhtml"}:
        return "web"
    return "unknown"


def count_pdf_pages(source: Path) -> int | None:
    try:
        from pypdf import PdfReader  # type: ignore

        return len(PdfReader(str(source)).pages)
    except Exception:
        pass
    try:
        import fitz  # type: ignore

        with fitz.open(str(source)) as doc:
            return doc.page_count
    except Exception:
        return None


def count_pptx_slides(source: Path) -> int | None:
    try:
        with ZipFile(source) as zf:
            return sum(
                1
                for item in zf.namelist()
                if re.fullmatch(r"ppt/slides/slide\d+\.xml", item)
            )
    except Exception:
        return None


def expected_units(source: Path) -> tuple[str, int | None]:
    kind = source_kind(source)
    if kind == "pdf":
        return "pages", count_pdf_pages(source)
    if kind == "slides" and source.suffix.lower() in {".pptx", ".ppsx"}:
        return "slides", count_pptx_slides(source)
    if kind == "image":
        return "images", 1
    return "units", None


def prepare_output(
    source: Path,
    output_root: Path,
    overwrite: bool,
    resume: bool = False,
) -> Path:
    base = safe_name(source.stem)
    package_dir = output_root / base
    if resume:
        package_dir.mkdir(parents=True, exist_ok=True)
        return package_dir
    if package_dir.exists() and not overwrite:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        package_dir = output_root / f"{base}_{stamp}"
    if package_dir.exists() and overwrite:
        shutil.rmtree(package_dir)
    package_dir.mkdir(parents=True, exist_ok=True)
    return package_dir


def engine_order(engine: str) -> list[str]:
    if engine == "auto":
        return ["mineru", "docling", "markitdown", "pymupdf", "pypdf"]
    return [engine]


def remove_tree_within(path: Path, root: Path) -> None:
    resolved_path = path.resolve()
    resolved_root = root.resolve()
    resolved_path.relative_to(resolved_root)
    if resolved_path == resolved_root:
        raise ValueError(f"refusing to remove root directory: {resolved_root}")
    if resolved_path.exists():
        shutil.rmtree(resolved_path)


def build_mineru_command(
    exe: str,
    source: Path,
    output_dir: Path,
    backend: str,
    args: argparse.Namespace,
    page_range: tuple[int, int] | None = None,
) -> list[str]:
    command = [exe, "-p", str(source), "-o", str(output_dir)]
    is_legacy = Path(exe).name.lower().startswith("magic-pdf")
    if is_legacy:
        command.extend(["-m", "auto"])
    command.extend(["-b", backend])
    if source.suffix.lower() == ".pdf" and not is_legacy:
        method = {"auto": "auto", "on": "ocr", "off": "txt"}[args.ocr]
        command.extend(["-m", method])
    if page_range is not None:
        command.extend(["-s", str(page_range[0]), "-e", str(page_range[1])])
    if backend.endswith("-http-client"):
        command.extend(["-u", str(args.mineru_url)])
    return command


def write_json_atomic(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def mineru_batch_signature(
    source: Path,
    backend: str,
    args: argparse.Namespace,
) -> dict[str, object]:
    stat = source.stat()
    return {
        "source": str(source),
        "source_size": stat.st_size,
        "source_mtime_ns": stat.st_mtime_ns,
        "backend": backend,
        "ocr": args.ocr,
        "batch_size": args.mineru_batch_size,
    }


def merge_mineru_batch_content_lists(
    raw_dir: Path,
    records: list[dict[str, object]],
) -> Path:
    merged: list[dict[str, object]] = []
    for record in sorted(records, key=lambda item: int(item["start_page"])):
        if record.get("status") != "completed":
            continue
        content_list = raw_dir / str(record["content_list"])
        items = json.loads(content_list.read_text(encoding="utf-8"))
        if not isinstance(items, list):
            raise ValueError(f"invalid MinerU content list: {content_list}")
        start_page = int(record["start_page"])
        end_page = int(record["end_page"])
        page_indices = [
            int(item["page_idx"])
            for item in items
            if isinstance(item, dict) and isinstance(item.get("page_idx"), int)
        ]
        relative_indices = bool(page_indices) and min(page_indices) >= 0 and max(
            page_indices
        ) <= end_page - start_page
        for item in items:
            if not isinstance(item, dict):
                continue
            copied = dict(item)
            if relative_indices and isinstance(copied.get("page_idx"), int):
                copied["page_idx"] = int(copied["page_idx"]) + start_page
            merged.append(copied)

    output = raw_dir / "__research_doc_ingest_merged_content_list.json"
    write_json_atomic(output, merged)
    return output


def run_mineru_batched(
    exe: str,
    source: Path,
    raw_dir: Path,
    backend: str,
    args: argparse.Namespace,
    page_count: int,
) -> EngineResult:
    manifest_path = raw_dir / "batch_manifest.json"
    signature = mineru_batch_signature(source, backend, args)
    records: list[dict[str, object]] = []
    if args.resume and manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("signature") != signature:
            return EngineResult(
                "mineru",
                raw_dir,
                ["mineru", "--resume"],
                2,
                stderr="MinerU resume signature does not match source or options",
            )
        records = list(manifest.get("batches") or [])

    completed = {
        (int(item["start_page"]), int(item["end_page"])): item
        for item in records
        if item.get("status") == "completed"
        and (raw_dir / str(item.get("content_list", ""))).exists()
    }
    all_stdout: list[str] = []
    all_stderr: list[str] = []
    warnings = [
        f"MinerU recoverable batching enabled: {args.mineru_batch_size} pages per batch"
    ]
    last_command: list[str] = ["mineru"]

    for start_page in range(0, page_count, args.mineru_batch_size):
        end_page = min(page_count - 1, start_page + args.mineru_batch_size - 1)
        key = (start_page, end_page)
        if key in completed:
            print(
                f"[research-doc-ingest] Reusing MinerU pages "
                f"{start_page + 1}-{end_page + 1}",
                file=sys.stderr,
                flush=True,
            )
            continue

        batch_dir = raw_dir / "batches" / f"{start_page + 1:04d}-{end_page + 1:04d}"
        remove_tree_within(batch_dir, raw_dir)
        batch_dir.mkdir(parents=True, exist_ok=True)
        command = build_mineru_command(
            exe,
            source,
            batch_dir,
            backend,
            args,
            (start_page, end_page),
        )
        last_command = command
        print(
            f"[research-doc-ingest] MinerU batch "
            f"{start_page + 1}-{end_page + 1} of {page_count}",
            file=sys.stderr,
            flush=True,
        )
        batch_started = time.monotonic()
        code, stdout, stderr = run_command_streaming(
            command,
            timeout_seconds=max(0, args.mineru_timeout_seconds),
            stall_timeout_seconds=max(0, args.mineru_stall_timeout_seconds),
            heartbeat_seconds=max(0, args.mineru_heartbeat_seconds),
        )
        all_stdout.append(stdout)
        all_stderr.append(stderr)
        records = [
            item
            for item in records
            if (int(item["start_page"]), int(item["end_page"])) != key
        ]
        record: dict[str, object] = {
            "start_page": start_page,
            "end_page": end_page,
            "status": "error",
            "returncode": code,
            "command": command,
            "elapsed_seconds": round(time.monotonic() - batch_started, 3),
        }
        if code == 0:
            content_list = select_mineru_content_list(batch_dir)
            if content_list is None:
                code = 4
                record["returncode"] = code
                record["error"] = "MinerU batch completed without a content list"
            else:
                record["status"] = "completed"
                record["content_list"] = str(content_list.relative_to(raw_dir))
        records.append(record)
        write_json_atomic(
            manifest_path,
            {"signature": signature, "batches": records},
        )
        merge_mineru_batch_content_lists(raw_dir, records)
        if code != 0:
            return EngineResult(
                "mineru",
                raw_dir,
                command,
                code,
                tail("".join(all_stdout)),
                tail("".join(all_stderr)),
                warnings,
            )

    merge_mineru_batch_content_lists(raw_dir, records)
    if args.resume and completed:
        warnings.append(f"Reused {len(completed)} completed MinerU batches")
    return EngineResult(
        "mineru",
        raw_dir,
        last_command,
        0,
        tail("".join(all_stdout)),
        tail("".join(all_stderr)),
        warnings,
    )


def run_mineru(source: Path, raw_dir: Path, args: argparse.Namespace) -> EngineResult:
    exe = command_path("mineru") or command_path("magic-pdf")
    if not exe:
        return EngineResult("mineru", raw_dir, ["mineru"], 127, stderr="mineru not found")

    backend = resolve_mineru_backend(args.mineru_backend)
    if backend.endswith("-http-client") and not args.mineru_url:
        return EngineResult(
            "mineru",
            raw_dir,
            ["mineru", "-b", backend],
            2,
            stderr=f"MinerU backend {backend} requires --mineru-url",
        )

    def run_once(selected_backend: str) -> EngineResult:
        raw_dir.mkdir(parents=True, exist_ok=True)
        print(
            f"[research-doc-ingest] MinerU backend: {selected_backend} "
            f"(requested: {args.mineru_backend})",
            file=sys.stderr,
            flush=True,
        )
        page_count = count_pdf_pages(source) if source.suffix.lower() == ".pdf" else None
        if (
            page_count
            and args.mineru_batch_size > 0
            and page_count > args.mineru_batch_size
        ):
            result = run_mineru_batched(
                exe,
                source,
                raw_dir,
                selected_backend,
                args,
                page_count,
            )
            if args.mineru_backend == "auto":
                result.warnings.append(f"MinerU backend auto-selected: {selected_backend}")
            return result

        command = build_mineru_command(exe, source, raw_dir, selected_backend, args)
        code, stdout, stderr = run_command_streaming(
            command,
            timeout_seconds=max(0, args.mineru_timeout_seconds),
            stall_timeout_seconds=max(0, args.mineru_stall_timeout_seconds),
            heartbeat_seconds=max(0, args.mineru_heartbeat_seconds),
        )
        warnings = []
        if code == 0:
            # Single-shot runs skip batching, but downstream scripts
            # (select_semantic_pages.py) still expect the merged content list.
            content_list = select_mineru_content_list(raw_dir)
            if content_list is not None:
                record = {
                    "start_page": 0,
                    "end_page": (page_count - 1) if page_count else 0,
                    "status": "completed",
                    "content_list": str(content_list.relative_to(raw_dir)),
                }
                merge_mineru_batch_content_lists(raw_dir, [record])
            else:
                warnings.append("MinerU completed without a content list")
        if args.mineru_backend == "auto":
            warnings.append(f"MinerU backend auto-selected: {selected_backend}")
        return EngineResult(
            "mineru",
            raw_dir,
            command,
            code,
            tail(stdout),
            tail(stderr),
            warnings,
        )

    result = run_once(backend)
    if (
        args.mineru_backend == "auto"
        and result.returncode != 0
        and backend != "pipeline"
    ):
        print(
            f"[research-doc-ingest] MinerU backend {backend} failed; "
            "retrying with pipeline",
            file=sys.stderr,
            flush=True,
        )
        retry = run_once("pipeline")
        retry.warnings.append(
            f"MinerU backend {backend} failed; fell back to pipeline"
        )
        return retry
    return result


def run_docling(source: Path, raw_dir: Path, args: argparse.Namespace) -> EngineResult:
    exe = command_path("docling")
    if not exe:
        return EngineResult("docling", raw_dir, ["docling"], 127, stderr="docling not found")

    raw_dir.mkdir(parents=True, exist_ok=True)
    common = [
        exe,
        str(source),
        "--to",
        "md",
        "--to",
        "json",
        "--output",
        str(raw_dir),
        "--image-export-mode",
        "referenced",
    ]
    if args.ocr == "off":
        common.append("--no-ocr")
    common.extend(args.docling_extra_arg)

    attempts = [
        [*common, "--to", "chunks"],
        common,
    ]
    last = EngineResult("docling", raw_dir, attempts[0], 1)
    for command in attempts:
        code, stdout, stderr = run_command(command)
        last = EngineResult("docling", raw_dir, command, code, tail(stdout), tail(stderr))
        if code == 0:
            return last
    return last


def run_markitdown(source: Path, raw_dir: Path, _args: argparse.Namespace) -> EngineResult:
    exe = command_path("markitdown")
    if not exe:
        return EngineResult(
            "markitdown", raw_dir, ["markitdown"], 127, stderr="markitdown not found"
        )

    raw_dir.mkdir(parents=True, exist_ok=True)
    command = [exe, str(source)]
    code, stdout, stderr = run_command(command)
    if code == 0:
        (raw_dir / f"{safe_name(source.stem)}.md").write_text(stdout, encoding="utf-8")
    return EngineResult("markitdown", raw_dir, command, code, tail(stdout), tail(stderr))


def run_pymupdf(source: Path, raw_dir: Path, _args: argparse.Namespace) -> EngineResult:
    if source.suffix.lower() != ".pdf":
        return EngineResult(
            "pymupdf", raw_dir, ["pymupdf"], 126, stderr="PyMuPDF fallback only supports PDF"
        )
    try:
        import fitz  # type: ignore
    except Exception as exc:
        return EngineResult(
            "pymupdf", raw_dir, ["pymupdf"], 127, stderr=f"PyMuPDF not available: {exc}"
        )

    raw_dir.mkdir(parents=True, exist_ok=True)
    snapshots_dir = raw_dir / "page_snapshots"
    snapshots_dir.mkdir(exist_ok=True)
    output = raw_dir / f"{safe_name(source.stem)}.md"
    warnings: list[str] = [
        "Built-in PyMuPDF fallback used: page text and page snapshots only; no OCR, table reconstruction, formula recognition, or actual visual understanding."
    ]
    try:
        lines = [f"# {source.name}", ""]
        with fitz.open(str(source)) as doc:
            for index, page in enumerate(doc, start=1):
                text = page.get_text("text") or ""
                snapshot_name = f"page_{index:03d}.png"
                snapshot_path = snapshots_dir / snapshot_name
                pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                pix.save(str(snapshot_path))
                lines.extend(
                    [
                        f"## Page {index}",
                        "",
                        text.strip() or "[No extractable text]",
                        "",
                        f"![Page {index} snapshot](page_snapshots/{snapshot_name})",
                        "",
                    ]
                )
        output.write_text("\n".join(lines), encoding="utf-8")
        return EngineResult("pymupdf", raw_dir, ["pymupdf", str(source)], 0, warnings=warnings)
    except Exception as exc:
        return EngineResult("pymupdf", raw_dir, ["pymupdf", str(source)], 1, stderr=str(exc))


def run_pypdf(source: Path, raw_dir: Path, _args: argparse.Namespace) -> EngineResult:
    if source.suffix.lower() != ".pdf":
        return EngineResult("pypdf", raw_dir, ["pypdf"], 126, stderr="pypdf fallback only supports PDF")
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception as exc:
        return EngineResult("pypdf", raw_dir, ["pypdf"], 127, stderr=f"pypdf not available: {exc}")

    raw_dir.mkdir(parents=True, exist_ok=True)
    output = raw_dir / f"{safe_name(source.stem)}.md"
    warnings: list[str] = [
        "Built-in pypdf fallback used: text extraction only, no layout reconstruction, OCR, image extraction, table reconstruction, or visual understanding."
    ]
    try:
        reader = PdfReader(str(source))
        lines = [f"# {source.name}", ""]
        for index, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            lines.extend([f"## Page {index}", "", text.strip() or "[No extractable text]", ""])
        output.write_text("\n".join(lines), encoding="utf-8")
        return EngineResult("pypdf", raw_dir, ["pypdf", str(source)], 0, warnings=warnings)
    except Exception as exc:
        return EngineResult("pypdf", raw_dir, ["pypdf", str(source)], 1, stderr=str(exc))


def run_engine(name: str, source: Path, raw_parent: Path, args: argparse.Namespace) -> EngineResult:
    raw_dir = raw_parent / name
    if name == "mineru":
        return run_mineru(source, raw_dir, args)
    if name == "docling":
        return run_docling(source, raw_dir, args)
    if name == "markitdown":
        return run_markitdown(source, raw_dir, args)
    if name == "pymupdf":
        return run_pymupdf(source, raw_dir, args)
    if name == "pypdf":
        return run_pypdf(source, raw_dir, args)
    raise ValueError(f"unknown engine: {name}")


def find_files(root: Path, suffixes: Iterable[str]) -> list[Path]:
    wanted = {s.lower() for s in suffixes}
    return sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in wanted)


def select_markdown(raw_dir: Path) -> Path | None:
    candidates = find_files(raw_dir, [".md", ".markdown"])
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_size)


def select_mineru_content_list(raw_dir: Path) -> Path | None:
    candidates = [
        p
        for p in find_files(raw_dir, [".json"])
        if p.name.endswith("_content_list.json") and not p.name.endswith("_content_list_v2.json")
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_size)


def caption_text(item: dict[str, object], keys: Iterable[str]) -> str:
    values: list[str] = []
    for key in keys:
        value = item.get(key)
        if isinstance(value, list):
            values.extend(str(v).strip() for v in value if str(v).strip())
        elif isinstance(value, str) and value.strip():
            values.append(value.strip())
    return "; ".join(values)


def render_mineru_content_list(content_list_path: Path, raw_dir: Path) -> Path | None:
    try:
        items = json.loads(content_list_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(items, list):
        return None

    pages: dict[int, list[dict[str, object]]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        page_idx = item.get("page_idx")
        if isinstance(page_idx, int):
            pages.setdefault(page_idx, []).append(item)

    if not pages:
        return None

    lines: list[str] = []
    for page_idx in sorted(pages):
        lines.extend([f"## Page {page_idx + 1}", ""])
        for item in pages[page_idx]:
            item_type = str(item.get("type", "unknown"))
            text = str(item.get("text", "")).strip()
            if item_type == "text" and text:
                level = item.get("text_level")
                if isinstance(level, int) and 1 <= level <= 6:
                    lines.extend([f"{'#' * max(3, level)} {text}", ""])
                else:
                    lines.extend([text, ""])
            elif item_type in {"header", "footer", "page_number", "aside_text"} and text:
                lines.extend([f"*{item_type}: {text}*", ""])
            elif item_type == "table":
                caption = caption_text(item, ["table_caption"])
                lines.extend([f"### Table: {caption or 'untitled table'}", ""])
                table_body = str(item.get("table_body", "")).strip()
                if table_body:
                    lines.extend([table_body, ""])
                img_path = str(item.get("img_path", "")).strip()
                if img_path:
                    lines.extend([f"![Table visual]({img_path})", ""])
            elif item_type in {"image", "chart"}:
                caption = caption_text(
                    item,
                    ["image_caption", "chart_caption", "image_footnote", "chart_footnote"],
                )
                content = str(item.get("content", "")).strip()
                label = "Chart" if item_type == "chart" else "Image"
                lines.extend([f"### {label}: {caption or 'untitled visual'}", ""])
                if content:
                    lines.extend([content, ""])
                img_path = str(item.get("img_path", "")).strip()
                if img_path:
                    lines.extend([f"![{label} visual]({img_path})", ""])
            elif text:
                lines.extend([text, ""])

    output = raw_dir / "__research_doc_ingest_mineru_pages.md"
    output.write_text("\n".join(lines), encoding="utf-8")
    return output


def copy_assets(raw_dir: Path, package_dir: Path) -> list[dict[str, str]]:
    assets_dir = package_dir / "assets"
    assets_dir.mkdir(exist_ok=True)
    copied: list[dict[str, str]] = []
    asset_files = find_files(raw_dir, list(IMAGE_EXTENSIONS | TABLE_EXTENSIONS))
    for item in asset_files:
        relative = item.relative_to(raw_dir)
        target = assets_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        copied.append(
            {
                "source_path": str(item),
                "relative_path": relative.as_posix(),
                "asset_path": str(target.relative_to(package_dir)),
                "kind": "table" if item.suffix.lower() in TABLE_EXTENSIONS else "image",
            }
        )
    return copied


IMAGE_MD_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def asset_key(value: str) -> str:
    value = value.strip().strip("<>").strip('"').strip("'")
    value = value.split("#", 1)[0].split("?", 1)[0]
    value = unquote(value).replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value


def build_asset_lookup(assets: list[dict[str, str]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    name_counts: dict[str, int] = {}
    for item in assets:
        relative = item.get("relative_path", "")
        asset = item.get("asset_path", "")
        if not relative or not asset:
            continue
        lookup[asset_key(relative)] = asset.replace("\\", "/")
        posix_parts = PurePosixPath(relative.replace("\\", "/")).parts
        if len(posix_parts) >= 2:
            lookup["/".join(posix_parts[-2:])] = asset.replace("\\", "/")
        name_counts[Path(relative).name] = name_counts.get(Path(relative).name, 0) + 1
    for item in assets:
        relative = item.get("relative_path", "")
        asset = item.get("asset_path", "")
        name = Path(relative).name
        if name and name_counts.get(name) == 1:
            lookup[name] = asset.replace("\\", "/")
    return lookup


def rewrite_image_links(
    markdown: str,
    visual_policy: str,
    warnings: list[str],
    asset_lookup: dict[str, str],
) -> str:
    if visual_policy == "off":
        return markdown

    count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        alt = match.group(1).strip() or "visual element"
        path = match.group(2).strip()
        asset_path = asset_lookup.get(asset_key(path), path)
        warnings.append(
            f"Visual element {count} requires textual verification: {asset_path}"
        )
        return (
            f"\n\n**Visual element {count}: {alt}**\n\n"
            f"- Asset: `{asset_path}`\n"
            "- Textual description: TODO review the asset or engine output and "
            "replace this note with a complete description.\n"
            "- Uncertainty: description not yet verified.\n\n"
        )

    return IMAGE_MD_RE.sub(replace, markdown)


def build_content(
    source: Path,
    result: EngineResult,
    markdown_path: Path,
    package_dir: Path,
    args: argparse.Namespace,
    warnings: list[str],
    assets: list[dict[str, str]],
) -> str:
    markdown = markdown_path.read_text(encoding="utf-8", errors="replace")
    if not args.no_rewrite_image_links:
        markdown = rewrite_image_links(
            markdown, args.visual_policy, warnings, build_asset_lookup(assets)
        )

    unit_name, unit_count = expected_units(source)
    summary_lines = [
        f"# {source.name}",
        "",
        "## Conversion Summary",
        "",
        f"- Source: `{source}`",
        f"- Source type: {source_kind(source)}",
        f"- Engine: {result.engine}",
        f"- Mode: {args.mode}",
        f"- OCR preference: {args.ocr}",
        f"- Expected {unit_name}: {unit_count if unit_count is not None else 'unknown'}",
        f"- Images embedded: no",
        f"- Package directory: `{package_dir}`",
        "",
    ]
    if warnings:
        summary_lines.append(f"- Warnings: {len(warnings)} recorded; see `audit.md` for the full list")
        for warning in warnings[:8]:
            summary_lines.append(f"  - {warning}")
        if len(warnings) > 8:
            summary_lines.append(f"  - ... {len(warnings) - 8} more warnings omitted from summary")
        summary_lines.append("")
    else:
        summary_lines.append("- Warnings: none recorded")
        summary_lines.append("")

    return "\n".join(summary_lines) + "\n---\n\n" + markdown


def chunk_markdown(content: str, source: Path, max_chars: int = 2400) -> list[dict[str, object]]:
    chunks: list[dict[str, object]] = []
    current_unit: str | None = None
    buffer: list[str] = []
    chunk_index = 0

    def flush() -> None:
        nonlocal buffer, chunk_index
        text = "\n".join(buffer).strip()
        if not text:
            buffer = []
            return
        chunks.append(
            {
                "chunk_index": chunk_index,
                "source": str(source),
                "page_or_slide": current_unit,
                "type": "markdown",
                "text": text,
            }
        )
        chunk_index += 1
        buffer = []

    for line in content.splitlines():
        match = re.match(r"^##\s+(Page|Slide)\s+(\d+)\b", line, flags=re.IGNORECASE)
        if match:
            flush()
            current_unit = f"{match.group(1).lower()}:{match.group(2)}"
        if sum(len(x) + 1 for x in buffer) + len(line) > max_chars:
            flush()
        buffer.append(line)
    flush()
    return chunks


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_audit(
    path: Path,
    source: Path,
    result: EngineResult,
    assets: list[dict[str, str]],
    chunks: list[dict[str, object]],
    warnings: list[str],
    attempts: list[EngineResult],
) -> None:
    unit_name, unit_count = expected_units(source)
    lines = [
        f"# Conversion Audit: {source.name}",
        "",
        f"- Source: `{source}`",
        f"- Source type: {source_kind(source)}",
        f"- Engine selected: {result.engine}",
        f"- Expected {unit_name}: {unit_count if unit_count is not None else 'unknown'}",
        f"- Assets copied: {len(assets)}",
        f"- Chunks written: {len(chunks)}",
        f"- Warnings: {len(warnings)}",
        "",
        "## Engine Attempts",
        "",
    ]
    for attempt in attempts:
        lines.append(f"- {attempt.engine}: return code {attempt.returncode}")
        lines.append(f"  - Command: `{' '.join(attempt.command)}`")
        if attempt.stderr:
            lines.append(f"  - Stderr tail: `{attempt.stderr[:500].replace('`', '')}`")
    lines.extend(["", "## Warnings", ""])
    if warnings:
        lines.extend(f"- {warning}" for warning in warnings)
    else:
        lines.append("- None recorded.")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def copy_raw_output(raw_parent: Path, package_dir: Path) -> str | None:
    if not raw_parent.exists():
        return None
    target = package_dir / "engine_output"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(raw_parent, target)
    return str(target.relative_to(package_dir))


def ensure_localhost_no_proxy() -> None:
    """Bypass proxies for loopback so local engine APIs (e.g. MinerU's
    mineru-api on 127.0.0.1) stay reachable when a system proxy is set."""
    hosts = ["127.0.0.1", "localhost", "::1"]
    for var in ("no_proxy", "NO_PROXY"):
        existing = [item.strip() for item in os.getenv(var, "").split(",") if item.strip()]
        merged = existing + [host for host in hosts if host not in existing]
        os.environ[var] = ",".join(merged)


def main() -> int:
    args = parse_args()
    ensure_localhost_no_proxy()
    source = Path(args.source).expanduser().resolve()
    if not source.exists():
        print(f"Source not found: {source}", file=sys.stderr)
        return 2
    if args.resume and args.overwrite:
        print("--resume and --overwrite cannot be used together.", file=sys.stderr)
        return 2
    if args.resume and args.mineru_batch_size <= 0:
        print("--resume requires --mineru-batch-size greater than 0.", file=sys.stderr)
        return 2

    output_root = Path(args.output_dir).expanduser().resolve()
    package_dir = prepare_output(source, output_root, args.overwrite, args.resume)
    warnings: list[str] = []
    attempts: list[EngineResult] = []

    persistent_work = args.mineru_batch_size > 0 and args.engine in {"auto", "mineru"}
    if persistent_work:
        raw_parent = package_dir / ".engine_work"
        if raw_parent.exists() and not args.resume:
            remove_tree_within(raw_parent, package_dir)
        raw_parent.mkdir(parents=True, exist_ok=True)
        work_context = contextlib.nullcontext(str(raw_parent))
    else:
        work_context = tempfile.TemporaryDirectory(prefix="research-doc-ingest-")

    with work_context as tmp:
        raw_parent = Path(tmp)
        selected: EngineResult | None = None
        for name in engine_order(args.engine):
            result = run_engine(name, source, raw_parent, args)
            attempts.append(result)
            if result.ok:
                selected = result
                break

        if selected is None:
            write_audit(package_dir / "audit.md", source, attempts[-1], [], [], warnings, attempts)
            print("No conversion engine succeeded. See audit.md.", file=sys.stderr)
            return 3

        warnings.extend(selected.warnings)

        markdown_path = None
        if selected.engine == "mineru":
            content_list_path = select_mineru_content_list(selected.raw_dir)
            if content_list_path:
                markdown_path = render_mineru_content_list(content_list_path, selected.raw_dir)
        if markdown_path is None:
            markdown_path = select_markdown(selected.raw_dir)
        if markdown_path is None:
            warnings.append("No Markdown output found in engine output.")
            write_audit(package_dir / "audit.md", source, selected, [], [], warnings, attempts)
            print("Engine succeeded but no Markdown file was found. See audit.md.", file=sys.stderr)
            return 4

        assets = [] if args.mode == "single-md" else copy_assets(selected.raw_dir, package_dir)
        if args.mode != "single-md" and not assets and args.visual_policy != "off":
            warnings.append("No visual/table assets were copied; verify whether the source had visual content.")

        content = build_content(
            source, selected, markdown_path, package_dir, args, warnings, assets
        )
        content_path = package_dir / "content.md"
        content_path.write_text(content, encoding="utf-8")

        chunks = chunk_markdown(content, source)
        chunks_path = package_dir / "chunks.jsonl"
        if args.mode != "single-md":
            write_jsonl(chunks_path, chunks)

        raw_rel = copy_raw_output(raw_parent, package_dir) if args.keep_raw else None
        structure = {
            "source": str(source),
            "source_type": source_kind(source),
            "engine": selected.engine,
            "command": selected.command,
            "expected_units": {
                "kind": expected_units(source)[0],
                "count": expected_units(source)[1],
            },
            "mode": args.mode,
            "visual_policy": args.visual_policy,
            "artifacts": {
                "content_md": "content.md",
                "chunks_jsonl": "chunks.jsonl" if args.mode != "single-md" else None,
                "audit_md": "audit.md",
                "assets": "assets" if assets else None,
                "engine_output": raw_rel,
            },
            "assets": assets,
            "warnings": warnings,
            "attempts": [
                {
                    "engine": item.engine,
                    "returncode": item.returncode,
                    "command": item.command,
                    "stderr_tail": item.stderr,
                }
                for item in attempts
            ],
        }
        if args.mode != "single-md":
            (package_dir / "structure.json").write_text(
                json.dumps(structure, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        write_audit(package_dir / "audit.md", source, selected, assets, chunks, warnings, attempts)

    if persistent_work and raw_parent.exists():
        remove_tree_within(raw_parent, package_dir)

    if args.visual_policy == "strict" and warnings:
        print(f"Wrote package with strict-policy warnings: {package_dir}", file=sys.stderr)
        return 5

    print(f"Wrote package: {package_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
