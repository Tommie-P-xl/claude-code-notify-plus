"""Text safety helpers shared by notification channels."""

from typing import Any


def sanitize_text(text: Any) -> str:
    """Return a UTF-8 serializable string, replacing invalid surrogates."""
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    return text.encode("utf-8", errors="replace").decode("utf-8")


def sanitize_data(value: Any) -> Any:
    """Recursively sanitize strings inside JSON-like data."""
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, list):
        return [sanitize_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_data(item) for item in value)
    if isinstance(value, dict):
        return {sanitize_text(key): sanitize_data(item) for key, item in value.items()}
    return value
