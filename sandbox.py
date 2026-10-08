"""Optional path sandboxing: confine file operations to a root directory.

The sandbox is off unless ``FS_ROOT`` is set. It exists to constrain an untrusted
caller (the LLM), so ``llm_file_assistant.py`` always turns it on; code calling
the tools directly is unrestricted by default.
"""

from __future__ import annotations

import os
from pathlib import Path

from errors import ToolError


def get_root() -> Path | None:
    """Sandbox root from the FS_ROOT env var, or None when no sandbox is configured.

    Read on every call (not at import time) so the root can change at runtime.
    An empty value counts as unset.
    """
    value = os.environ.get("FS_ROOT")
    return Path(value).expanduser().resolve() if value else None


def resolve_path(path: str) -> Path:
    """Resolve ``path``; with a sandbox root set, relative paths are anchored to it
    and the result must stay inside it."""
    root = get_root()
    p = Path(path).expanduser()
    if root is None:
        return p.resolve()
    if not p.is_absolute():
        p = root / p
    p = p.resolve()
    if p != root and root not in p.parents:
        raise ToolError(f"Access denied: '{path}' is outside the allowed directory")
    return p
