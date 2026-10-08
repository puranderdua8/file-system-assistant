"""File system tools for the resume assistant.

Every tool returns JSON-serialisable data and never raises: failures come back
as ``{"success": False, "error": "..."}`` so an LLM can read and react to them.

All paths are confined to a sandbox root (see ``sandbox.py``). Text extraction
per file type lives in ``parsers.py``.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from errors import ToolError, error_response
from parsers import extract_text
from sandbox import resolve_path
from utils import iso_timestamp

WRITABLE_EXTENSIONS = {".txt", ".md", ".json", ".csv"}
MAX_CONTENT_CHARS = 20_000
MAX_MATCHES = 50
CONTEXT_CHARS = 80


def read_file(filepath: str) -> dict:
    """Read a resume (.pdf, .txt, .md, .docx) and return its text and metadata."""
    try:
        path = resolve_path(filepath)
        text, extra = extract_text(path)
        stat = path.stat()
        result = {
            "success": True,
            "filepath": str(path),
            "filename": path.name,
            "file_type": path.suffix.lower().lstrip("."),
            "content": text[:MAX_CONTENT_CHARS],
            "truncated": len(text) > MAX_CONTENT_CHARS,
            "metadata": {
                "size_bytes": stat.st_size,
                "modified": iso_timestamp(stat.st_mtime),
                "char_count": len(text),
                "word_count": len(text.split()),
                **extra,
            },
        }
        if not text:
            result["warning"] = "No text extracted (file may be empty or a scanned image)"
        return result
    except Exception as exc:  # noqa: BLE001 - tools must never raise
        return error_response(exc)


def list_files(directory: str, extension: str | None = None) -> list:
    """List files in a directory, optionally filtered by extension (e.g. '.pdf').

    Returns a list of {name, path, size_bytes, modified}. Hidden files (names
    starting with '.', such as .gitkeep or .DS_Store) are not listed. On failure
    returns a one-item list ``[{"success": False, "error": ...}]`` to keep the
    list shape.
    """
    try:
        path = resolve_path(directory)
        if not path.exists():
            raise ToolError(f"Directory not found: {directory}")
        if not path.is_dir():
            raise ToolError(f"Not a directory: {directory}")
        ext = None
        if extension:
            ext = extension.lower()
            if not ext.startswith("."):
                ext = "." + ext
        items = []
        for child in sorted(path.iterdir(), key=lambda c: c.name.lower()):
            if not child.is_file() or child.name.startswith("."):
                continue
            if ext and child.suffix.lower() != ext:
                continue
            stat = child.stat()
            items.append(
                {
                    "name": child.name,
                    "path": str(child),
                    "size_bytes": stat.st_size,
                    "modified": iso_timestamp(stat.st_mtime),
                }
            )
        return items
    except Exception as exc:  # noqa: BLE001
        return [error_response(exc)]


def write_file(filepath: str, content: str) -> dict:
    """Write text to a file, creating parent directories. Overwrites existing files."""
    try:
        path = resolve_path(filepath)
        ext = path.suffix.lower()
        if ext not in WRITABLE_EXTENSIONS:
            raise ToolError(
                f"Cannot write '{ext or '(none)'}' files. "
                f"Allowed: {', '.join(sorted(WRITABLE_EXTENSIONS))}"
            )
        if path.is_dir():
            raise ToolError(f"Path is a directory: {filepath}")
        existed = path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        # Atomic write: temp file in the same directory, then rename over target.
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return {
            "success": True,
            "filepath": str(path),
            "bytes_written": len(data),
            "overwrote": existed,
        }
    except Exception as exc:  # noqa: BLE001
        return error_response(exc)


def search_in_file(filepath: str, keyword: str) -> dict:
    """Case-insensitive keyword search; returns each hit with surrounding context."""
    try:
        if not keyword or not keyword.strip():
            raise ToolError("Keyword must not be empty")
        path = resolve_path(filepath)
        text, _ = extract_text(path)
        matches = []
        total = 0
        for m in re.finditer(re.escape(keyword), text, re.IGNORECASE):
            total += 1
            if len(matches) >= MAX_MATCHES:
                continue
            start = max(0, m.start() - CONTEXT_CHARS)
            end = min(len(text), m.end() + CONTEXT_CHARS)
            context = " ".join(text[start:end].split())
            matches.append(
                {
                    "line_number": text.count("\n", 0, m.start()) + 1,
                    "match": m.group(0),
                    "context": ("..." if start > 0 else "")
                    + context
                    + ("..." if end < len(text) else ""),
                }
            )
        return {
            "success": True,
            "filepath": str(path),
            "keyword": keyword,
            "match_count": total,
            "matches": matches,
            "truncated": total > len(matches),
        }
    except Exception as exc:  # noqa: BLE001
        return error_response(exc)
