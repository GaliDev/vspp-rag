from __future__ import annotations

import re
from typing import Any


def compact_manifest_header(record: dict[str, Any]) -> str:
    title = record.get("title") or record.get("external_id") or "Untitled"
    return f"# {title}"


def strip_manifest_preamble(text: str) -> str:
    """Remove normalize.py metadata block (title line + key: value lines)."""
    lines = text.splitlines()
    if not lines or not lines[0].startswith("# "):
        return text
    i = 1
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            break
        if ":" in line and not line.startswith("#"):
            i += 1
            continue
        break
    while i < len(lines) and not lines[i].strip():
        i += 1
    return "\n".join(lines[i:]).lstrip()


def content_with_header(record: dict[str, Any], body: str) -> str:
    header = compact_manifest_header(record)
    body = body.strip()
    if not body:
        return f"{header}\n"
    return f"{header}\n\n{body}\n"
