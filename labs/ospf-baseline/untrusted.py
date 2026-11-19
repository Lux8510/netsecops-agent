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


def bound(text: str, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    """Keep head and tail, mark truncation; never silently drop the middle."""
    if limit < 200:
        raise ValueError("limit is too small to preserve head and tail")
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return {"text": text, "truncated": False, "original_bytes": len(encoded)}
    head = max(1, (limit * 2) // 3)
    tail = max(1, limit - head)
    clipped = (text.encode("utf-8")[:head].decode("utf-8", "ignore")
               + "\n…[truncated]…\n"
               + text.encode("utf-8")[-tail:].decode("utf-8", "ignore"))
    return {"text": clipped, "truncated": True, "original_bytes": len(encoded)}
