from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path
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

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "points": self.points,
            "priority": self.priority,
            "announcement_text": self.announcement_text,
            "audio_file": self.audio_file,
        }


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

    def check_region(self, x1n: float, y1n: float, x2n: float, y2n: float) -> Optional[ROI]:
        """Return highest-priority ROI that intersects the normalized rectangle, or None."""
        from shapely.geometry import box as shapely_box
        region = shapely_box(x1n, y1n, x2n, y2n)
        matched = [
            r for r in self.rois
            if len(r.points) >= 3 and Polygon(r.points).intersects(region)
        ]
        return min(matched, key=lambda r: r.priority) if matched else None

    def remove(self, name: str):
        self.rois = [r for r in self.rois if r.name != name]

    def clear(self):
        self.rois.clear()

    def save(self, path: str) -> None:
        data = {"rois": [r.to_dict() for r in self.rois]}
        Path(path).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @classmethod
    def load(cls, path: str) -> ROIManager:
        manager = cls()
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            for item in data.get("rois", []):
                color = _COLORS[len(manager.rois) % len(_COLORS)]
                manager.rois.append(ROI(
                    name=item["name"],
                    points=item["points"],
                    priority=item["priority"],
                    announcement_text=item["announcement_text"],
                    color=color,
                    audio_file=item.get("audio_file", ""),
                ))
        except (FileNotFoundError, KeyError, json.JSONDecodeError):
            pass
        return manager
