"""fp_hotspots.py — 배경 오탐지 다발 지점(hotspot) 누적 (Pi 로컬 sqlite, 카메라별 파일 분리).

`detection_events.py`와 같은 원칙으로 `profile.traffic_db` 파일에 별도 테이블을 만든다 —
카메라별로 이미 db 파일이 분리돼 있어(카메라당 1개) 그 원칙을 그대로 재사용한다.

기록 대상은 **정지 억제(`STATIC_CANE_SUPPRESS_FRAMES`)로 이미 걸러낸 지팡이 트랙**이다.
움직이지 않는 지팡이는 배경의 케이블/문틀 경계선/난간 같은 고정 지형지물 오탐지가 거의
확실하므로, 그 위치를 누적해두면 "여기에 제외구역을 만드시겠습니까?"라고 제안할 수 있다.
카메라가 고정이라 같은 지형지물은 항상 같은 화면 좌표에 나타난다는 점을 이용한다.

좌표는 정규화(0~1)로 받아 `GRID`×`GRID` 셀로 양자화해 셀 단위로 카운트를 누적한다 —
트랙마다 bbox가 미세하게 흔들려도 같은 셀로 모이게 하기 위해서다. 셀 안에서의 실제
bbox는 누적 평균으로 유지해, 제외구역 폴리곤을 제안할 때 쓴다.

자동으로 제외구역을 만들지는 않는다 — roi_editor UI가 사람에게 확인을 받는다.
지팡이 사용자가 늘 같은 지점에서 멈춰 서면 그 위치도 핫스팟으로 잡힐 수 있기 때문이다.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Union

GRID = 32                    # 화면을 GRID x GRID 셀로 나눠 집계
_MIN_COUNT_DEFAULT = 30      # 이만큼 쌓여야 제안 후보로 올린다 (실기기에서 재조정 필요)
_LIMIT_DEFAULT = 5


def _cell(cx: float, cy: float) -> tuple[int, int]:
    """정규화 중심 좌표 -> 그리드 셀 인덱스 (경계값 1.0도 마지막 셀에 들어가게 clamp)."""
    return (
        min(GRID - 1, max(0, int(cx * GRID))),
        min(GRID - 1, max(0, int(cy * GRID))),
    )


def log_suppressed(
    db_path: Union[str, Path],
    x1: float, y1: float, x2: float, y2: float,
) -> None:
    """정지 억제된 지팡이 탐지 1건을 기록. 좌표는 모두 정규화(0~1) bbox.

    호출부는 **트랙 id당 1회만** 부르는 책임이 있다 — 매 프레임 쓰면 sqlite I/O가
    탐지 루프를 막는다.
    """
    cx, cy = _cell((x1 + x2) / 2, (y1 + y2) / 2)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fp_hotspots (
                cell_x INTEGER NOT NULL,
                cell_y INTEGER NOT NULL,
                count  INTEGER NOT NULL,
                sum_x1 REAL NOT NULL,
                sum_y1 REAL NOT NULL,
                sum_x2 REAL NOT NULL,
                sum_y2 REAL NOT NULL,
                PRIMARY KEY (cell_x, cell_y)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO fp_hotspots (cell_x, cell_y, count, sum_x1, sum_y1, sum_x2, sum_y2)
            VALUES (?, ?, 1, ?, ?, ?, ?)
            ON CONFLICT (cell_x, cell_y) DO UPDATE SET
                count  = count  + 1,
                sum_x1 = sum_x1 + excluded.sum_x1,
                sum_y1 = sum_y1 + excluded.sum_y1,
                sum_x2 = sum_x2 + excluded.sum_x2,
                sum_y2 = sum_y2 + excluded.sum_y2
            """,
            (cx, cy, x1, y1, x2, y2),
        )
        conn.commit()
    finally:
        conn.close()


def read_hotspots(
    db_path: Union[str, Path],
    min_count: int = _MIN_COUNT_DEFAULT,
    limit: int = _LIMIT_DEFAULT,
) -> list[dict]:
    """카운트 상위 핫스팟을 반환. db/테이블이 아직 없으면 빈 리스트.

    bbox는 누적 평균(정규화 0~1)이며, roi_editor가 이걸 사각 폴리곤으로 만들어
    제외구역 후보로 제안한다.
    """
    try:
        conn = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return []

    try:
        rows = conn.execute(
            """
            SELECT cell_x, cell_y, count, sum_x1, sum_y1, sum_x2, sum_y2
            FROM fp_hotspots WHERE count >= ?
            ORDER BY count DESC LIMIT ?
            """,
            (min_count, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        conn.close()

    return [
        {
            "cell": [cell_x, cell_y],
            "count": count,
            "bbox": [sx1 / count, sy1 / count, sx2 / count, sy2 / count],
        }
        for cell_x, cell_y, count, sx1, sy1, sx2, sy2 in rows
    ]


def clear_hotspots(db_path: Union[str, Path]) -> None:
    """누적을 초기화한다 — 제외구역을 만들었거나 카메라 위치/회전을 바꾼 뒤 호출."""
    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.OperationalError:
        return
    try:
        conn.execute("DELETE FROM fp_hotspots")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    finally:
        conn.close()
