"""Shared helpers for API modules — eliminates duplicated _error() and _iso() definitions."""


def api_error(code: str, message: str) -> dict:
    """Build a structured error dict for HTTPException detail."""
    return {"code": code, "message": message, "details": None}


def iso_format(dt) -> str:
    """Format datetime as ISO 8601 string.

    Returns "" for None input (matches original _iso behaviour).
    Uses strftime for consistent UTC format matching the original _iso().
    """
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
