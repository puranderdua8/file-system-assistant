"""Shared exception types and their tool-response representation."""


class ToolError(Exception):
    """Expected, user-presentable failure inside a tool."""


def error_response(exc: Exception) -> dict:
    """Convert an exception into the standard failure dict returned by tools."""
    if isinstance(exc, ToolError):
        return {"success": False, "error": str(exc)}
    return {"success": False, "error": f"{type(exc).__name__}: {exc}"}
