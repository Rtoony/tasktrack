"""Convex hull (Andrew's monotone chain) for project_sites polygons.

Used by /api/v1/projects/hulls to render multi-site projects as a
translucent polygon in the Map tab. Pure-Python stdlib only — no
shapely or numpy. Input/output are sequences of (x, y) tuples where
x = longitude and y = latitude. The implementation does not care
about projection; it just produces the CCW-ordered hull of the input.

Algorithm: O(n log n) due to the initial sort. The hull itself is
two passes. For our worst case (84 sites in one project) this is
trivial.

References:
    https://en.wikipedia.org/wiki/Convex_hull_algorithms#Andrew's_monotone_chain
"""
from __future__ import annotations

from typing import Iterable, Sequence


Point = tuple[float, float]


def _cross(o: Point, a: Point, b: Point) -> float:
    """2-D cross product of OA and OB vectors. >0 = left turn."""
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def convex_hull(points: Iterable[Point]) -> list[Point]:
    """Return the CCW-ordered convex hull of `points`.

    - 0 input points -> []
    - 1 input point  -> [p]
    - 2 input points -> [p1, p2] (degenerate hull = the line segment)
    - 3+ collinear points -> just the two extreme points (also a segment)
    - 3+ non-collinear points -> proper polygon, no duplicate endpoint

    Duplicate input points are collapsed before processing.
    """
    pts: list[Point] = sorted(set((float(x), float(y)) for x, y in points))
    n = len(pts)
    if n <= 1:
        return pts
    if n == 2:
        return pts

    # Build lower hull.
    lower: list[Point] = []
    for p in pts:
        while len(lower) >= 2 and _cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)

    # Build upper hull.
    upper: list[Point] = []
    for p in reversed(pts):
        while len(upper) >= 2 and _cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)

    # Concatenate, dropping the last point of each list because it's
    # repeated as the first of the other list.
    return lower[:-1] + upper[:-1]


def hull_geojson_ring(points: Sequence[Point]) -> list[list[float]] | None:
    """Build a closed GeoJSON LinearRing from the input points.

    Returns None when there are too few points to form a polygon
    (caller is expected to fall back to LineString or skip the row).
    GeoJSON polygons require the first and last positions to match.
    """
    hull = convex_hull(points)
    if len(hull) < 3:
        return None
    ring = [[lng, lat] for lng, lat in hull]
    ring.append(ring[0])
    return ring
