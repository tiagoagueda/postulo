"""The world's outline as rings Leaflet can draw, for a map with no tiles (#898).

`templates/jobs/map/world_outline.html` is one SVG path, Natural Earth's 1:110m boundaries in
an equirectangular 360 by 180 plane (#108), written only with absolute *move*, *line* and
*close* commands. In that plane a unit is a degree, so a point is ``(90 - y, x - 180)`` in
latitude and longitude and nothing is lost turning it back. Read once per process; the file
stays the one copy of the drawing, so the outline under the SVG and under Leaflet cannot
drift apart.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

OUTLINE = Path(__file__).resolve().parents[1] / "templates" / "jobs" / "map" / "world_outline.html"

_PATH = re.compile(r'\sd="([^"]+)"')
_COMMAND = re.compile(r"([MLZ])\s*(-?[\d.]+)?\s*(-?[\d.]+)?")


@lru_cache(maxsize=1)
def rings() -> tuple[tuple[tuple[float, float], ...], ...]:
    """Every closed ring of the outline as ``(latitude, longitude)`` pairs.

    Drawn as one polygon with the even-odd rule, as the SVG is, so a lake inside a
    continent stays a hole.
    """
    found = _PATH.search(OUTLINE.read_text(encoding="utf-8"))
    if not found:
        return ()
    result: list[tuple[tuple[float, float], ...]] = []
    ring: list[tuple[float, float]] = []
    for command, x, y in _COMMAND.findall(found.group(1)):
        if command == "M":
            if ring:
                result.append(tuple(ring))
            ring = [(round(90.0 - float(y), 1), round(float(x) - 180.0, 1))]
        elif command == "L":
            ring.append((round(90.0 - float(y), 1), round(float(x) - 180.0, 1)))
        elif ring:
            result.append(tuple(ring))
            ring = []
    if ring:
        result.append(tuple(ring))
    return tuple(result)
