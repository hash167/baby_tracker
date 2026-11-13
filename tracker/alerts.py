"""Windowed alert rules. A single sample over the line does not fire."""

from __future__ import annotations


def sustained(
    points: list[tuple[float, float]],
    *,
    window_seconds: float,
    threshold: float,
    minimum: int,
) -> bool:
    """`points` are `(age_seconds, value)` where age 0 is the newest sample."""
    in_window = [value for age, value in points if 0 <= age <= window_seconds]
    if len(in_window) < minimum:
        return False
    return all(value >= threshold for value in in_window)


# Documented thresholds. Live Honeycomb triggers use the same numbers.
RULES = {
    'http_5xx_ratio': {'window_seconds': 300, 'threshold': 0.05, 'minimum': 3},
    'db_errors': {'window_seconds': 300, 'threshold': 1, 'minimum': 3},
    'save_failures': {'window_seconds': 600, 'threshold': 1, 'minimum': 3},
    'import_failures': {'window_seconds': 900, 'threshold': 1, 'minimum': 1},
    'backup_missing': {'window_seconds': 93600, 'threshold': 1, 'minimum': 1},
}
