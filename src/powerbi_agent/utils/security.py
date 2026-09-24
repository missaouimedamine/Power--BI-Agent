"""Security helpers: secret redaction, path confinement and untrusted-data fencing."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

REDACTED = "**********"

_SECRET_KEY_PATTERN = re.compile(
    r"(secret|password|passwd|pwd|token|api[_-]?key|credential|authorization|conn(ection)?_?str)",
    re.IGNORECASE,
)

# Values that look like secrets even under an innocuous key.
_SECRET_VALUE_PATTERNS = [
    re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE),
    re.compile(r"(?i)(password|pwd)\s*=\s*[^;]+"),
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
]


def is_secret_key(key: str) -> bool:
    return bool(_SECRET_KEY_PATTERN.search(key))


def redact_text(text: str) -> str:
    for pattern in _SECRET_VALUE_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


def redact(value: Any) -> Any:
    """Recursively mask secret-looking keys and values in mappings, lists and strings."""
    if isinstance(value, Mapping):
        return {
            k: (REDACTED if isinstance(k, str) and is_secret_key(k) and v else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return type(value)(redact(v) for v in value)
    if isinstance(value, str):
        return redact_text(value)
    return value


class PathOutsideWorkspaceError(ValueError):
    """Raised when a path escapes the directory it is confined to."""


def confine_path(base: Path, candidate: Path | str) -> Path:
    """Resolve ``candidate`` relative to ``base`` and refuse anything outside ``base``."""
    base_resolved = base.resolve()
    target = (base_resolved / candidate).resolve()
    if not target.is_relative_to(base_resolved):
        raise PathOutsideWorkspaceError(f"{candidate!s} escapes {base_resolved!s}")
    return target


_MAX_QUOTED = 80
_MD_SPECIAL = str.maketrans({"|": "\\|", "[": "\\[", "]": "\\]", "<": "&lt;", ">": "&gt;"})


def escape_md_text(text: str) -> str:
    """Make data-derived text inert in Markdown (no tables broken, links, HTML or code)."""
    text = " ".join(str(text).split()).replace("`", "'")
    return text.translate(_MD_SPECIAL)


def quote_data_value(value: object, max_chars: int = _MAX_QUOTED) -> str:
    """Render a data value (or column name) as a bounded, inert Markdown code span.

    Used for every data-derived string that ends up in human- or LLM-facing text, so a
    value like ``Ignore previous instructions `rm -rf` `` reads as quoted data.
    """
    text = " ".join(str(value).split()).replace("`", "'").replace("|", "\\|")
    if len(text) > max_chars:
        text = text[: max_chars - 1] + "…"
    return f"`{text}`" if text else "`(blank)`"


UNTRUSTED_OPEN = "<untrusted_data>"
UNTRUSTED_CLOSE = "</untrusted_data>"


def fence_untrusted(text: str, source: str = "user data") -> str:
    """Wrap data values before they go into an LLM prompt.

    Data read from files, databases or models is content, not instructions. Agents'
    prompts tell the model that anything inside these tags must never be followed.
    Closing tags inside the payload are neutralised so the fence cannot be broken.
    """
    safe = text.replace(UNTRUSTED_CLOSE, "</untrusted_data_>")
    return f'{UNTRUSTED_OPEN[:-1]} source="{source}">\n{safe}\n{UNTRUSTED_CLOSE}'
