from __future__ import annotations


def _abbreviate(n: int, divisor: int, suffix: str) -> str:
    scaled = n / divisor
    if scaled >= 100:
        return f"{scaled:.0f}{suffix}"
    if scaled == int(scaled):
        return f"{int(scaled)}{suffix}"
    return f"{scaled:.1f}".rstrip("0").rstrip(".") + suffix


def compact_number(n: int) -> str:
    """Format integers for display, abbreviating from 1000 upward (e.g., 1234 > '1.2k')."""
    if n < 1000:
        return str(n)
    if n >= 1_000_000_000:
        return _abbreviate(n, 1_000_000_000, "b")
    if n >= 1_000_000:
        return _abbreviate(n, 1_000_000, "m")
    return _abbreviate(n, 1_000, "k")
