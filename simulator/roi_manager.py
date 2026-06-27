from dataclasses import dataclass, field
from shapely.geometry import Polygon, Point
from typing import Optional

# BGR colors cycled per ROI
_COLORS = [
    (0, 220, 0),      # green
    (255, 140, 0),    # orange
    (0, 200, 255),    # yellow
    (255, 0, 200),    # magenta
    (0, 255, 200),    # cyan-green
]


@dataclass
class ROI:
    name: str
    points: list          # normalized [[x, y], ...] in [0, 1]
    priority: int
    announcement_text: str
    color: tuple = field(default_factory=lambda: _COLORS[0])
    audio_file: str = ""  # MP3 경로 (없으면 빈 문자열)


class ROIManager:
    def __init__(self):
        self.rois: list[ROI] = []

    def add_roi(self, name: str, points: list, priority: int,
                announcement_text: str, audio_file: str = ""):
        color = _COLORS[len(self.rois) % len(_COLORS)]
        self.rois.append(ROI(
            name=name,
            points=points,
            priority=priority,
            announcement_text=announcement_text,
            color=color,
            audio_file=audio_file,
        ))

    def check(self, cx_norm: float, cy_norm: float) -> Optional[ROI]:
        """Return highest-priority ROI containing the point, or None."""
        point = Point(cx_norm, cy_norm)
        matched = [
            r for r in self.rois
            if len(r.points) >= 3 and Polygon(r.points).contains(point)
        ]
        return min(matched, key=lambda r: r.priority) if matched else None

    def remove(self, name: str):
        self.rois = [r for r in self.rois if r.name != name]

    def clear(self):
        self.rois.clear()
