"""static_mask.py — 현장 고정 구조물을 **객체 단위로** 기억해 오탐을 막는다.

## 왜 필요한가

설치 현장에는 그 현장에만 있는 구조물(기둥·개찰구·안내판·손잡이)이 있고, 어떤
홀드아웃 데이터셋도 그걸 대표할 수 없다. 문을 닫은 새벽에 몇 분 관측하면 **사람이
없으므로 탐지되는 모든 것이 정의상 오탐**이라, 라벨링 없이 그 현장의 네거티브가
공짜로 생긴다.

## 기존 제외구역(`ROI.zone_type="exclude"`)과 무엇이 다른가

제외구역은 **bbox 중심점이 폴리곤 안인가**로 판정한다(`roi_manager.is_excluded`).
점 하나만 보므로 **기둥을 딱 맞게 그려도 그 앞에 선 사람의 중심점이 같은 자리**라
함께 지워지고, `camera_live_pi._filter_excluded`가 클래스 구분 없이 적용하므로
사람 탐지까지 사라진다. 사람 동반 게이트는 **모든 트리거의 필수 조건**이라 이는
곧 안내 실패다.

구조물은 항상 **같은 박스**(위치+크기+종횡비)를 만들고, 앞을 지나는 사람은 다른
모양의 박스를 만든다. 그래서 판정을 점에서 박스로 올린다:

    기존: 중심점 ∈ 폴리곤                       → 버림
    여기: IoU(det, 기억된 박스) ≥ IOU_MATCH  AND  같은 클래스 → **표시**

근거는 움직임 게이트의 실측이다(`CLAUDE.md`) — *"±2px 지터로 300프레임 뒤에도
3.9px, 이동 물체는 30프레임에 114px"*. 구조물 박스는 프레임 간 거의 완전히 일치한다.

## ★ 버리지 않고 "표시"만 하는 이유

마스크는 **움직임 게이트의 사전확률**이지 최종 판정이 아니다. 여기서 하는 일은
`d["static_masked"] = True`를 붙이는 것뿐이고, 실제로 떨어뜨릴지는 `gate_chain`이
**트랙이 움직였는지**까지 보고 정한다.

raw 단계에서 바로 버리면 안 되는 이유가 둘이다:

1. `max_disp`(원점 대비 최대 변위)는 **트래킹 이후에만** 존재한다. 움직임으로
   마스크를 무효화하려면 트랙 상태가 필요하다.
2. 그렇다고 트래킹 이후에 하드 드롭하면 `_filter_excluded` docstring이 적어둔
   문제가 생긴다 — *"트래킹 이후에 거르면 구역 경계에서 트랙이 깜빡인다"*(EMA
   스무딩/coasting 때문).

표시만 해 두면 트래킹은 평소대로 돌고, 기둥 앞을 **걸어가는** 사람이나 지팡이
사용자는 트랙이 움직이므로 자동으로 살아난다.

> **잔여 위험**: 마스크된 자리에 **가만히 서 있는** 사람은 지워진다. 그래서
> 사람 클래스 마스크는 수집만 하고 **기본 적용하지 않는다** — 운영자가 목록에서
> 보고 개별로 켠다.

## 저장 구조

`fp_hotspots.py`와 같은 원칙이다 — 카메라별 `traffic_db` 파일에 별도 테이블, WAL,
읽기는 read-only uri, **절대 예외를 던지지 않는다**(탐지 루프에서 불린다).

- **sqlite**: 수집 원본(후보 박스·횟수·이동량·최고 conf·썸네일)과 마스크 적중 로그
- **`static_mask.json`**: 운영자가 실제로 켠 것만. `rois.json`과 같은 자리에 두고
  같은 mtime 폴링으로 핫리로드된다 — 새 메커니즘을 만들지 않는다.

자동으로 적용하지 않는 이유는 `fp_hotspots.py` 헤더와 같다 — 캘리브레이션 중
청소·보수 인력이 지나가면 그 자리가 구조물로 굳어 **사각지대**가 된다.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Iterable, Union

__all__ = [
    "IOU_MATCH", "IOU_CLUSTER", "MIN_HIT_RATIO",
    "StaticMask", "MaskCollector",
    "save_candidates", "read_candidates", "clear_candidates",
    "log_mask_hit", "read_mask_hits", "clear_mask_hits",
    "load_mask_file", "save_mask_file", "default_mask_path",
]

# 런타임 매칭 기준. 구조물 박스는 프레임 간 거의 일치하므로(위 실측) 0.6이면
# 넉넉하다. 더 올리면 탐지 박스의 미세한 흔들림에 마스크가 빗나가고, 더 내리면
# 그 앞을 지나는 사람까지 걸리기 시작한다.
IOU_MATCH = 0.6

# 수집 중 같은 구조물로 묶는 기준. 매칭보다 느슨하게 둬서 흔들리는 박스가 여러
# 클러스터로 쪼개지지 않게 한다.
IOU_CLUSTER = 0.5

# 수집한 프레임 중 이 비율 이상에서 보여야 후보로 올린다. 한두 번 스쳐간 것을
# 구조물로 굳히지 않기 위한 하한이다.
MIN_HIT_RATIO = 0.3

_FILENAME = "static_mask.json"

_SCHEMA_CANDIDATES = """
CREATE TABLE IF NOT EXISTS static_mask_candidates (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    cls        INTEGER NOT NULL,
    x1 REAL NOT NULL, y1 REAL NOT NULL, x2 REAL NOT NULL, y2 REAL NOT NULL,
    hits       INTEGER NOT NULL,
    frames     INTEGER NOT NULL,
    max_disp   REAL NOT NULL,      -- 정규화 최대 변위. 진짜 고정물은 0에 가깝다
    max_conf   REAL NOT NULL,
    thumb      TEXT,               -- 썸네일 파일명 (운영자 판단 근거)
    collected_at REAL NOT NULL,
    rotation   INTEGER,            -- 수집 당시 카메라 설정 — 바뀌면 좌표가 어긋난다
    preset     TEXT
)
"""

_SCHEMA_HITS = """
CREATE TABLE IF NOT EXISTS static_mask_hits (
    cls   INTEGER NOT NULL,
    count INTEGER NOT NULL,
    last_ts REAL NOT NULL,
    PRIMARY KEY (cls)
)
"""


# ---------------------------------------------------------------------------
# 기하
# ---------------------------------------------------------------------------
def iou(a: Iterable[float], b: Iterable[float]) -> float:
    """정규화 [x1, y1, x2, y2] 두 박스의 IoU."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    x1, y1 = max(ax1, bx1), max(ay1, by1)
    x2, y2 = min(ax2, bx2), min(ay2, by2)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _norm_box(det: dict, w: int, h: int) -> list[float]:
    x1, y1, x2, y2 = det["bbox"]
    return [x1 / w, y1 / h, x2 / w, y2 / h]


# ---------------------------------------------------------------------------
# 런타임
# ---------------------------------------------------------------------------
class StaticMask:
    """운영자가 켠 구조물 박스 목록. 탐지에 `static_masked` 표시를 붙인다."""

    def __init__(self, boxes: list[dict] | None = None,
                 iou_match: float = IOU_MATCH) -> None:
        # boxes: [{"cls": int, "bbox": [x1,y1,x2,y2] 정규화}, ...]
        self.boxes = list(boxes or [])
        self.iou_match = iou_match

    def __len__(self) -> int:
        return len(self.boxes)

    def matches(self, det: dict, frame_w: int, frame_h: int) -> bool:
        """이 탐지가 기억된 구조물과 같은 자리·같은 크기·같은 클래스인가."""
        if not self.boxes:
            return False
        try:
            box = _norm_box(det, frame_w, frame_h)
            cls = det.get("class")
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            return False
        for m in self.boxes:
            if m.get("cls") != cls:
                continue
            if iou(box, m.get("bbox", (0, 0, 0, 0))) >= self.iou_match:
                return True
        return False

    def annotate(self, dets: list[dict], frame_w: int, frame_h: int) -> list[dict]:
        """각 탐지에 `static_masked`를 붙여 그대로 돌려준다 — **버리지 않는다.**

        실제 제거는 `gate_chain`이 트랙의 움직임까지 보고 결정한다(모듈 docstring).
        """
        for d in dets:
            d["static_masked"] = self.matches(d, frame_w, frame_h)
        return dets


# ---------------------------------------------------------------------------
# 수집 (캘리브레이션)
# ---------------------------------------------------------------------------
class MaskCollector:
    """폐장 시간 관측 중 탐지를 클러스터로 누적한다.

    사람이 없는 시간에 돌리는 것이 전제이므로 **클래스를 가리지 않고 전부 모은다** —
    무엇을 실제로 적용할지는 운영자가 목록을 보고 정한다.
    """

    def __init__(self, iou_cluster: float = IOU_CLUSTER) -> None:
        self.iou_cluster = iou_cluster
        self.frames = 0
        # [{cls, sum_box[4], hits, first_center, max_disp, max_conf, thumb}]
        self._clusters: list[dict] = []

    def add(self, dets: list[dict], frame_w: int, frame_h: int) -> list[int]:
        """한 프레임의 탐지를 누적. 새로 만들어진 클러스터의 인덱스를 돌려준다.

        반환값은 호출부가 **그 클러스터의 썸네일을 한 장만** 저장하는 데 쓴다.
        """
        self.frames += 1
        new: list[int] = []
        for d in dets:
            try:
                box = _norm_box(d, frame_w, frame_h)
                cls = d.get("class")
                conf = float(d.get("conf", 0.0))
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                continue
            idx = self._find(cls, box)
            if idx is None:
                cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
                self._clusters.append({
                    "cls": cls, "sum": list(box), "hits": 1,
                    "origin": (cx, cy), "max_disp": 0.0, "max_conf": conf,
                    "thumb": None,
                })
                new.append(len(self._clusters) - 1)
            else:
                c = self._clusters[idx]
                for i in range(4):
                    c["sum"][i] += box[i]
                c["hits"] += 1
                c["max_conf"] = max(c["max_conf"], conf)
                cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
                ox, oy = c["origin"]
                # 누적 경로가 아니라 **원점 대비 변위**다 — 누적은 지터가 계속
                # 더해져 고정물도 결국 "움직였다"가 된다(움직임 게이트와 같은 논리).
                c["max_disp"] = max(c["max_disp"], ((cx - ox) ** 2 + (cy - oy) ** 2) ** 0.5)
        return new

    def set_thumb(self, index: int, name: str) -> None:
        if 0 <= index < len(self._clusters):
            self._clusters[index]["thumb"] = name

    def _find(self, cls, box) -> int | None:
        best, best_iou = None, self.iou_cluster
        for i, c in enumerate(self._clusters):
            if c["cls"] != cls:
                continue
            mean = [v / c["hits"] for v in c["sum"]]
            v = iou(box, mean)
            if v >= best_iou:
                best, best_iou = i, v
        return best

    def finish(self, min_hit_ratio: float = MIN_HIT_RATIO) -> list[dict]:
        """후보 목록. 관측 프레임 중 일정 비율 이상 보인 것만 남긴다."""
        if self.frames <= 0:
            return []
        out = []
        for c in self._clusters:
            if c["hits"] / self.frames < min_hit_ratio:
                continue
            out.append({
                "cls": c["cls"],
                "bbox": [round(v / c["hits"], 6) for v in c["sum"]],
                "hits": c["hits"],
                "frames": self.frames,
                "max_disp": round(c["max_disp"], 6),
                "max_conf": round(c["max_conf"], 4),
                "thumb": c["thumb"],
            })
        out.sort(key=lambda r: -r["hits"])
        return out


# ---------------------------------------------------------------------------
# sqlite — 후보
# ---------------------------------------------------------------------------
def _connect(db_path: Union[str, Path]) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_SCHEMA_CANDIDATES)
    conn.execute(_SCHEMA_HITS)
    conn.commit()
    return conn


def save_candidates(db_path: Union[str, Path], rows: list[dict],
                    rotation: int | None = None, preset: str | None = None) -> int:
    """수집 결과로 후보 테이블을 **교체**한다(이전 수집분은 지운다).

    누적이 아니라 교체인 이유: 캘리브레이션은 "지금 이 현장의 상태"를 찍는 것이고,
    광고판 교체나 임시 구조물 때문에 이전 수집이 낡았을 수 있다.
    """
    try:
        conn = _connect(db_path)
    except Exception as exc:                      # noqa: BLE001
        print(f"[WARN] 구조물 후보 저장 실패: {exc}")
        return 0
    try:
        now = time.time()
        conn.execute("DELETE FROM static_mask_candidates")
        conn.executemany(
            "INSERT INTO static_mask_candidates "
            "(cls, x1, y1, x2, y2, hits, frames, max_disp, max_conf, thumb,"
            " collected_at, rotation, preset) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(r["cls"], *r["bbox"], r["hits"], r["frames"], r["max_disp"],
              r["max_conf"], r.get("thumb"), now, rotation, preset) for r in rows])
        conn.commit()
        return len(rows)
    except Exception as exc:                      # noqa: BLE001
        print(f"[WARN] 구조물 후보 저장 실패: {exc}")
        return 0
    finally:
        conn.close()


def read_candidates(db_path: Union[str, Path]) -> list[dict]:
    """후보 목록. db/테이블이 아직 없으면 빈 리스트."""
    try:
        conn = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return []
    try:
        rows = conn.execute(
            "SELECT id, cls, x1, y1, x2, y2, hits, frames, max_disp, max_conf,"
            " thumb, collected_at, rotation, preset"
            " FROM static_mask_candidates ORDER BY hits DESC").fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        conn.close()
    return [
        {"id": i, "cls": c, "bbox": [x1, y1, x2, y2], "hits": hits, "frames": fr,
         "max_disp": md, "max_conf": mc, "thumb": th, "collected_at": ts,
         "rotation": rot, "preset": ps}
        for (i, c, x1, y1, x2, y2, hits, fr, md, mc, th, ts, rot, ps) in rows
    ]


def clear_candidates(db_path: Union[str, Path]) -> None:
    try:
        conn = _connect(db_path)
    except Exception:                             # noqa: BLE001
        return
    try:
        conn.execute("DELETE FROM static_mask_candidates")
        conn.commit()
    except Exception:                             # noqa: BLE001
        pass
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# sqlite — 적중 로그
# ---------------------------------------------------------------------------
def log_mask_hit(db_path: Union[str, Path], cls: int, now: float | None = None) -> None:
    """마스크가 트랙 하나를 걸러냈음을 기록한다.

    **호출부는 트랙 id당 1회만 부를 책임이 있다** — 매 프레임 쓰면 sqlite I/O가
    탐지 루프를 막는다(`fp_hotspots.log_suppressed`와 같은 계약).

    이 로그가 없으면 "안내가 조용해진 것이 오탐이 줄어서인지 사람을 못 봐서인지"를
    구분할 수 없다 — 사각지대를 뒤늦게라도 발견하는 유일한 수단이다.
    """
    try:
        conn = _connect(db_path)
    except Exception:                             # noqa: BLE001
        return
    try:
        conn.execute(
            "INSERT INTO static_mask_hits (cls, count, last_ts) VALUES (?, 1, ?) "
            "ON CONFLICT(cls) DO UPDATE SET count = count + 1, last_ts = excluded.last_ts",
            (int(cls), now if now is not None else time.time()))
        conn.commit()
    except Exception:                             # noqa: BLE001
        pass
    finally:
        conn.close()


def read_mask_hits(db_path: Union[str, Path]) -> list[dict]:
    try:
        conn = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return []
    try:
        rows = conn.execute(
            "SELECT cls, count, last_ts FROM static_mask_hits ORDER BY cls").fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        conn.close()
    return [{"cls": c, "count": n, "last_ts": ts} for c, n, ts in rows]


def clear_mask_hits(db_path: Union[str, Path]) -> None:
    try:
        conn = _connect(db_path)
    except Exception:                             # noqa: BLE001
        return
    try:
        conn.execute("DELETE FROM static_mask_hits")
        conn.commit()
    except Exception:                             # noqa: BLE001
        pass
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# static_mask.json — 실제로 켜진 것만
# ---------------------------------------------------------------------------
def default_mask_path(base: Path | None = None) -> Path:
    """`rois.json`과 같은 자리(기기 루트)에 둔다 — 같은 mtime 폴링으로 핫리로드된다."""
    return (base or Path.cwd()) / _FILENAME


def load_mask_file(path: Union[str, Path]) -> StaticMask:
    """켜진 마스크를 읽는다. 없거나 깨졌으면 **빈 마스크**(= 현행 동작 그대로).

    예외를 던지지 않는다 — 마스크가 없다고 탐지·안내가 멈추면 안 된다.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return StaticMask([])
    if not isinstance(data, dict):
        return StaticMask([])
    boxes = []
    for b in data.get("boxes", []):
        try:
            bbox = [float(v) for v in b["bbox"]]
            if len(bbox) == 4:
                boxes.append({"cls": int(b["cls"]), "bbox": bbox})
        except (KeyError, TypeError, ValueError):
            continue
    return StaticMask(boxes)


def save_mask_file(path: Union[str, Path], boxes: list[dict],
                   meta: dict | None = None) -> None:
    """원자적 저장 — `rois.json`과 같은 방식.

    쓰는 도중 전원이 끊겨 반쪽 파일이 남으면 기기가 마스크를 잘못 읽는다.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"boxes": [{"cls": int(b["cls"]), "bbox": [float(v) for v in b["bbox"]]}
                         for b in boxes]}
    if meta:
        payload["meta"] = meta
    fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
