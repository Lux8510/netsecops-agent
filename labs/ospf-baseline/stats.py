"""Small-sample statistics helpers for the experiment matrix."""

from __future__ import annotations

import math


def wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval; behaves sensibly when n is small or p is 0/1."""
    if total <= 0:
        raise ValueError("total must be positive")
    if successes < 0 or successes > total:
        raise ValueError("successes must be within [0, total]")
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def rate(successes: int, total: int, *, minimum: int = 5) -> dict[str, float | int | bool | None]:
    """Return a rate with a Wilson interval.

    An empty sample is a valid "no data" state: rate/CI are null and the
    caller is told the sample is underpowered, instead of raising late.
    """
    if successes < 0:
        raise ValueError("successes must be non-negative")
    if total < 0:
        raise ValueError("total must be non-negative")
    if successes > total:
        raise ValueError("successes must be within [0, total]")
    if total == 0:
        return {"count": 0, "total": 0, "rate": None, "ci95_low": None,
                "ci95_high": None, "underpowered": True}
    low, high = wilson(successes, total)
    return {
        "count": successes,
        "total": total,
        "rate": round(successes / total, 4),
        "ci95_low": round(low, 4),
        "ci95_high": round(high, 4),
        "underpowered": total < minimum,
    }
