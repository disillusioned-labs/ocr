"""Line - the internal normalization target every provider converges to."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Line:
    text: str
    score: float
    bbox: tuple[int, int, int, int]  # x1, y1, x2, y2
    page: int = 1
