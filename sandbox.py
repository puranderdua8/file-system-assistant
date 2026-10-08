"""Path sandboxing: keep every file operation inside an allowed root directory."""

from __future__ import annotations

import os
from pathlib import Path

from errors import ToolError


def get_root() -> Path:
    """Sandbox root: the FS_ROOT env var, or the current directory.

    Read on every call (not at import time) so the root can change at runtime.
    """
    return Path(os.environ.get("FS_ROOT") or Path.cwd()).expanduser().resolve()


def resolve_path(path: str) -> Path:
    """Resolve ``path`` (relative to the sandbox root) and ensure it stays inside."""
    root = get_root()
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = root / p
    p = p.resolve()
    if p != root and root not in p.parents:
        raise ToolError(f"Access denied: '{path}' is outside the allowed directory")
    return p
