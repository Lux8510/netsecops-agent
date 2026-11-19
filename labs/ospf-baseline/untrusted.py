"""Treat device output as data, never as instructions.

Network banners, descriptions and interface comments are attacker-controlled in
many real environments. These helpers detect instruction-like text and bound
how much raw output is kept in a single evidence record.
"""

from __future__ import annotations

import re
from typing import Any

PATTERNS = (
    re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?", re.IGNORECASE),
    re.compile(r"disregard\s+(?:all\s+)?(?:previous|prior|above)", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+[a-z]", re.IGNORECASE),
    re.compile(r"(?:system|developer)\s+prompt", re.IGNORECASE),
    re.compile(r"report\s+(?:this\s+)?(?:as\s+)?normal", re.IGNORECASE),
    re.compile(r"忽略(?:之前|以上|所有)的?(?:指令|要求)"),
    re.compile(r"直接(?:报告|输出).{0,6}(?:正常|normal)"),
)
DEFAULT_LIMIT = 20_000


def scan(text: str) -> dict[str, Any]:
    matches = [match.group(0) for pattern in PATTERNS for match in pattern.finditer(text)]
    return {"count": len(matches), "matches": matches[:10]}


def scan_record(record: dict[str, Any]) -> dict[str, Any]:
    text = "\n".join(
        str(item.get(key) or "")
        for item in record.get("observations") or [] if isinstance(item, dict)
        for key in ("stdout", "stderr")
    )
    return scan(text)


MARKER = "\n…[truncated]…\n"


def bound(text: str, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    """Keep head and tail within the byte budget, marker included."""
    if limit < 200:
        raise ValueError("limit is too small to preserve head and tail")
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return {"text": text, "truncated": False, "original_bytes": len(encoded)}
    budget = limit - len(MARKER.encode("utf-8"))
    head = max(1, (budget * 2) // 3)
    tail = max(1, budget - head)
    clipped = (encoded[:head].decode("utf-8", "ignore") + MARKER
               + encoded[-tail:].decode("utf-8", "ignore"))
    return {"text": clipped, "truncated": True, "original_bytes": len(encoded)}


def bound_record(record: dict[str, Any], limit: int = 12_000) -> dict[str, Any]:
    """A model-facing copy with bounded stdout/stderr; the trace keeps the raw record."""
    result = dict(record)
    observations = []
    truncated = False
    for item in record.get("observations") or []:
        if not isinstance(item, dict):
            continue
        copy = dict(item)
        for key in ("stdout", "stderr"):
            value = copy.get(key)
            if isinstance(value, str):
                bounded = bound(value, limit)
                copy[key] = bounded["text"]
                copy[f"{key}_bytes"] = bounded["original_bytes"]
                copy[f"{key}_truncated"] = bounded["truncated"]
                truncated = truncated or bounded["truncated"]
        observations.append(copy)
    result["observations"] = observations
    result["output_truncated"] = truncated
    return result
