"""pedestrian_entity.py — 사람 트랙과 지팡이 트랙을 하나의 보행자로 묶어 추적한다.

`cane_person_assoc.py`는 스스로 밝히듯 **"이번 한 프레임"만** 본다. 그래서 지팡이
트랙과 사람 트랙이 끝까지 별개의 객체로 남고, 매 프레임 새로 짝을 맞춘다. 여기서
네 가지가 파생된다.

1. 지팡이가 한 프레임만 가려져도 그 프레임은 "사람 동반 아님"이 되어 트리거
   게이트가 막히고 디바운스가 리셋된다
2. 밀착한 두 사람 사이에서 지팡이 하나가 **양쪽 모두와** 짝지어진다
3. 유동인구의 지팡이 사용자 판정(동반 프레임 **비율**)이 트래킹이 좋아질수록
   불리해진다 — 분모에 "사람이 멀어 지팡이가 안 잡히는 구간"이 들어가기 때문
4. ROI 판정이 지팡이 박스에만 달려 있어, 지팡이를 놓치면 판정 자체가 사라진다

이 모듈은 사람+지팡이를 `PedestrianEntity` 하나로 묶고 **상태를 프레임 사이에
유지**해서 네 가지를 한꺼번에 닫는다. 사람 트랙은 지팡이보다 훨씬 오래 산다
(실측 최장 생존 360 vs 121프레임)는 비대칭이 이 설계의 근거다.

설계 제약 — 반드시 지킬 것
--------------------------

* **표준 라이브러리만 사용한다.** `simple_tracker.py`·`cane_person_assoc.py`와 같은
  원칙이다(Pi 배포 대상). numpy·cv2를 import하지 말 것.
* **I/O도 상태 외부 의존도 없는 순수 로직이다.** `tools/eval/eval_video_recall.py`가
  추론(1회)과 게이트(임계값마다 재실행)를 분리해 두었기 때문에, 이 레이어가 게이트
  단계의 순수 로직으로 남아야 임계값 스윕 A/B가 거의 공짜로 유지된다.
* **기하 판정은 여기서 하지 않는다.** `cane_person_assoc.candidate_pairs()`가 유일한
  출처이고, 이 모듈은 그 후보들 중 **어느 짝을 고를지**만 정한다.

순환 없는 게이트 배치
---------------------

래치의 입력은 **정지 억제 + 움직임 게이트를 통과한 지팡이**(`gated_cane_ids`)이고,
출력은 **사람 동반 게이트의 완화**다. 사람 동반 게이트의 결과를 입력으로 쓰면
순환이 생기므로 쓰지 않는다 — 이 분리가 설계의 핵심이다.

부수 효과로, 래치는 "이미 두 게이트를 통과한 지팡이"의 연장이 되어 배경 오탐지가
래치되는 경로가 닫힌다.

사람 트랙이 죽으면 엔티티도 죽는다
----------------------------------

`SimpleTracker`는 `max_age`(기본 10프레임)만큼 coasting으로 트랙을 살려둔 뒤
제거하며 **같은 track_id를 부활시키지 않는다**. 따라서 "사람 트랙이 사라졌다"는
곧 영구 소멸이고, 유예 기간을 두는 것은 의미가 없다 — 낡은 사람 bbox에 가상
지팡이를 계속 투영하는 위험만 남는다. 트래커의 coasting 자체가 이미 유예 기간
역할을 한다.
"""

from __future__ import annotations

from dataclasses import dataclass

from cane_person_assoc import CANE_CLASS_ID, PERSON_CLASS_ID, candidate_pairs

__all__ = [
    "LATCH_SEC",
    "OFFSET_ALPHA",
    "VIRTUAL_MAX_SEC",
    "PedestrianEntity",
    "EntityTracker",
    "latched_cane_ids",
    "cane_user_person_ids",
    "virtual_cane_boxes",
    "subject_for_canes",
]

# ★ 이 모듈의 시간 상수는 프레임이 아니라 **초**다.
#
# 기존 게이트 상수들(`STATIC_CANE_SUPPRESS_FRAMES`, `SimpleTracker.max_age`)은 프레임
# 단위라 촬영 프레임레이트에 따라 의미가 달라진다 — 평가 영상은 24~60 fps인데 Pi
# 실측은 12.76 fps라, 같은 "30프레임"이 0.5초에서 2.4초까지 벌어진다. 그래서 평가
# 영상에서 고른 값이 실기기로 전이되지 않는다. 이 모듈은 그 함정을 피하려고 초를 쓴다.

# 지팡이가 이만큼 **연속으로** 동반돼야 "지팡이 사용자"로 확정(래치)한다.
# 디바운스(0.5초)와 같은 수준 — 더 짧으면 스쳐 지나가는 단발 오연관이 래치되고,
# 더 길면 짧게 잡히는 실제 사용자를 놓친다.
LATCH_SEC = 0.4

# 지팡이-사람 상대 오프셋의 지수평활 계수(높을수록 최신 프레임에 빠르게 반응).
# 흰 지팡이는 짚는 동작으로 앞뒤를 오가므로 마지막 한 프레임 값만 쓰면 가상 박스가
# 그 위상에 따라 튄다. 평균 자세 쪽으로 눌러주는 값이다.
OFFSET_ALPHA = 0.5

# 가상 지팡이 박스를 만들어 주는 최대 시간 — 마지막으로 **실제 지팡이가 게이트를
# 통과한 순간**으로부터 잰다.
#
# 상한이 왜 필요한가: 없으면 래치된 엔티티가 사람 트랙이 사는 내내 가상 박스를 뿜는다.
# 실측에서 정확히 그 일이 벌어졌다 — 사람 트랙이 768~939프레임을 사는 실내 영상
# (test2/test3)에서 한 번의 래치가 수백 프레임을 통과시켜, 재현율은 6.1% -> 49.6%로
# 올랐지만 **정답 구간 밖 오탐율이 0.0% -> 39.2%, 헛트리거가 0 -> 3으로 뛰었다.**
#
# 값을 1.0초로 고른 이유:
#
# 1. **트래커의 coasting보다 길어야 의미가 있다.** `SimpleTracker`가 이미 `max_age`
#    (10프레임) 동안 마지막 bbox를 유지하는데, Pi 실측 12.76 FPS에서 그게 0.78초다.
#    이보다 짧게 잡으면 기기에서는 하는 일이 없다(60 fps 평가 영상에서는 0.17초라
#    짧은 값도 효과가 보이는데, 그 값을 그대로 기기에 가져가면 안 되는 이유다).
# 2. 배포 기본 operating point(conf 0.55)에서 test1의 최장 연속 통과가 90 -> 149프레임,
#    트리거 4 -> 5회로 늘고, 정답 구간이 있는 test2/test3에서는 **오탐율·헛트리거 변화가
#    전혀 없다**(그 임계값에서는 실내 탐지 자체가 0이라 래치가 걸리지 않는다).
# 3. 임계값을 낮춰 운용하면 대가가 생긴다 — conf 0.25에서 test3의 정답 구간 밖 오탐율이
#    1.4% -> 5.1%로 오르고 헛트리거가 0 -> 1이 된다. 늘리면 재현율과 오탐율이 **함께**
#    오르므로, 전체 스윕 표(`docs/model_evaluation_report_v3.md` §8)를 보고 조정할 것.
#    평가에서는 `--entity-virtual-sec`으로 바로 바꿔 잴 수 있다.
VIRTUAL_MAX_SEC = 1.0


@dataclass
class PedestrianEntity:
    """사람 트랙 1개 + (있으면) 짝지어진 지팡이 트랙 1개."""

    entity_id: int
    person_id: int
    person_bbox: list
    cane_id: int | None = None          # 이번 프레임에 짝지어진 지팡이 (없으면 None)
    cane_bbox: list | None = None
    is_cane_user: bool = False          # ★ 래치 — 엔티티가 사는 동안 유지된다
    pair_since: float | None = None     # 현재 연속 동반이 시작된 시각 (끊기면 None)
    last_cane_seen: float | None = None  # 실제 지팡이가 마지막으로 게이트를 통과한 시각
    frames: int = 0                     # 엔티티 생존 프레임 수
    # 사람 bbox 크기로 정규화한 지팡이의 상대 위치 (x1, y1, x2, y2).
    # 픽셀 절대값이 아니라 비율인 이유는 원근(가까우면 크게, 멀면 작게 찍힘)에 따라
    # 같은 오프셋이 유지되어야 하기 때문이다 — `max_gap_ratio`와 같은 논리.
    cane_offset: tuple[float, float, float, float] | None = None
    # 지팡이 트랙이 끊긴 구간에서 `cane_offset`을 현재 사람 bbox에 투영한 박스.
    virtual_cane_bbox: list | None = None


class EntityTracker:
    """프레임마다 트랙 목록을 받아 엔티티를 갱신한다.

    `SimpleTracker.update()`가 트랙 dict의 **복사본**을 돌려주므로(마지막 줄의
    `dict(t)`) 엔티티 상태를 트랙에 얹을 수 없다. 그래서 이 클래스가 별도 저장소를
    들고 `track_id`로 연결한다.
    """

    def __init__(self, latch_sec: float = LATCH_SEC,
                 offset_alpha: float = OFFSET_ALPHA,
                 virtual_max_sec: float = VIRTUAL_MAX_SEC) -> None:
        self.latch_sec = latch_sec
        self.offset_alpha = offset_alpha
        self.virtual_max_sec = virtual_max_sec
        self._by_person: dict[int, PedestrianEntity] = {}
        self._next_id = 0

    # ------------------------------------------------------------------ #

    def update(self, tracks: list[dict], gated_cane_ids: set[int] | None,
               now: float) -> list[PedestrianEntity]:
        """트랙 목록을 받아 엔티티를 갱신하고 이번 프레임의 엔티티들을 반환한다.

        `gated_cane_ids`는 **정지 억제 + 움직임 게이트를 통과한** 지팡이 track_id
        집합이다. 래치 카운터는 이 집합에 든 지팡이로만 올라간다(위 "순환 없는
        게이트 배치" 참고). `None`이면 모든 지팡이를 통과로 본다 — 게이트가 없는
        호출부를 위한 폴백이며, 배포 경로에서는 항상 넘긴다.

        `now`는 **초 단위 단조 시각**이다(배포는 `time.time()`, 평가는
        `프레임번호 / fps`). 필수 인자로 둔 이유는 위 "시간 상수는 초다" 참고 —
        프레임 수로 세면 이 값이 촬영 프레임레이트에 묶여 실기기로 전이되지 않는다.
        """
        canes, people, cands = candidate_pairs(tracks)
        gated = gated_cane_ids if gated_cane_ids is not None else {
            c["track_id"] for c in canes
        }

        person_bbox = {p["track_id"]: p["bbox"] for p in people}
        cane_bbox = {c["track_id"]: c["bbox"] for c in canes}

        pairing = self._assign(cands, gated)

        # 사람 트랙이 사라진 엔티티는 함께 소멸한다 (모듈 docstring 참고).
        self._by_person = {pid: e for pid, e in self._by_person.items()
                           if pid in person_bbox}

        out: list[PedestrianEntity] = []
        for pid, bbox in person_bbox.items():
            ent = self._by_person.get(pid)
            if ent is None:
                ent = PedestrianEntity(entity_id=self._next_id, person_id=pid,
                                       person_bbox=list(bbox))
                self._next_id += 1
                self._by_person[pid] = ent

            ent.person_bbox = list(bbox)
            ent.frames += 1
            cid = pairing.get(pid)
            ent.cane_id = cid
            ent.cane_bbox = list(cane_bbox[cid]) if cid is not None else None

            # 래치 카운터는 "게이트를 통과한 지팡이"와 **연속으로** 짝지어진
            # 프레임만 센다. 한 프레임이라도 끊기면 0으로 되돌린다 — 스쳐 지나가는
            # 단발 오연관이 누적돼 래치되는 것을 막기 위해서다.
            if cid is not None and cid in gated:
                if ent.pair_since is None:
                    ent.pair_since = now
                ent.last_cane_seen = now
                self._update_offset(ent)
                if now - ent.pair_since >= self.latch_sec:
                    ent.is_cane_user = True
            else:
                ent.pair_since = None

            # 가상 지팡이 박스 — 래치된 엔티티의 지팡이가 이번 프레임에 없고, 마지막
            # 실제 통과로부터 `virtual_max_sec` 이내일 때만 만든다. 상한이 왜
            # 필요한지는 그 상수의 주석 참고(없으면 오탐율이 40%p 뛴다).
            ent.virtual_cane_bbox = (
                self._project(ent)
                if (ent.is_cane_user and cid is None
                    and ent.last_cane_seen is not None
                    and now - ent.last_cane_seen <= self.virtual_max_sec)
                else None
            )
            out.append(ent)

        return out

    # ------------------------------------------------------------------ #

    def _assign(self, cands: list[tuple[int, int, float, float]],
                gated: set[int]) -> dict[int, int]:
        """후보 목록에서 person -> cane 의 **1:1** 배정을 만든다.

        `cane_person_assoc`의 프레임 단위 판정은 1:1이 아니라서, 밀착한 두 사람
        사이에서 지팡이 하나가 양쪽 모두와 짝지어진다. 엔티티는 "이 사람이 지팡이
        사용자인가"를 누적 판정하므로 그 모호함을 그대로 두면 옆 사람까지 지팡이
        사용자로 래치된다.

        두 단계로 정한다.

        1. **히스테리시스** — 직전 프레임의 (사람, 지팡이) 결합이 이번에도 후보에
           남아 있으면 무조건 유지한다. 없으면 매 프레임 정렬 결과가 미세한 거리
           변화로 뒤집혀, 나란히 걷는 두 사람 사이에서 지팡이가 왔다 갔다 하며
           양쪽의 래치 카운터를 번갈아 리셋한다.
        2. 남은 후보는 `(게이트 통과 여부, gap, 중심거리)` 오름차순 greedy 배정.
           **게이트를 통과한 지팡이를 먼저 배정하는 이유**는, 배경 기둥 같은
           비통과 지팡이가 1:1 자리를 선점해 진짜 지팡이를 밀어내는 것을 막기
           위해서다. `gap`은 지팡이를 쥐면 두 박스가 겹쳐 0이 되는 게 정상이라
           동점이 흔하므로, 중심거리를 2차 키로 쓴다.
        """
        cand_set = {(pid, cid) for pid, cid, _, _ in cands}
        taken_person: set[int] = set()
        taken_cane: set[int] = set()
        pairing: dict[int, int] = {}

        for pid, ent in self._by_person.items():
            if ent.cane_id is not None and (pid, ent.cane_id) in cand_set:
                pairing[pid] = ent.cane_id
                taken_person.add(pid)
                taken_cane.add(ent.cane_id)

        for pid, cid, gap, dist in sorted(
            cands, key=lambda c: (0 if c[1] in gated else 1, c[2], c[3])
        ):
            if pid in taken_person or cid in taken_cane:
                continue
            pairing[pid] = cid
            taken_person.add(pid)
            taken_cane.add(cid)

        return pairing

    def _update_offset(self, ent: PedestrianEntity) -> None:
        """사람 bbox 크기로 정규화한 지팡이 상대 위치를 지수평활로 갱신한다."""
        px1, py1, px2, py2 = ent.person_bbox
        pw, ph = px2 - px1, py2 - py1
        if pw <= 0 or ph <= 0 or ent.cane_bbox is None:
            return
        cx1, cy1, cx2, cy2 = ent.cane_bbox
        now = ((cx1 - px1) / pw, (cy1 - py1) / ph,
               (cx2 - px1) / pw, (cy2 - py1) / ph)
        if ent.cane_offset is None:
            ent.cane_offset = now
            return
        a = self.offset_alpha
        ent.cane_offset = tuple(
            a * n + (1 - a) * o for n, o in zip(now, ent.cane_offset)
        )

    @staticmethod
    def _project(ent: PedestrianEntity) -> list | None:
        """저장된 상대 오프셋을 현재 사람 bbox에 투영해 가상 지팡이 박스를 만든다.

        ROI 판정 코드(bbox 하단 10% strip → `check_region`)를 바꾸지 않고 **입력만**
        바꾸기 위한 것이다. 사람 발치를 그대로 쓰지 않는 이유는, 흰 지팡이는 몸
        앞으로 뻗어 짚어서 접지점이 발보다 앞서기 때문이다 — 발치로 재면 트리거
        타이밍이 뒤로 밀린다.
        """
        if ent.cane_offset is None:
            return None
        px1, py1, px2, py2 = ent.person_bbox
        pw, ph = px2 - px1, py2 - py1
        ox1, oy1, ox2, oy2 = ent.cane_offset
        return [round(px1 + ox1 * pw), round(py1 + oy1 * ph),
                round(px1 + ox2 * pw), round(py1 + oy2 * ph)]


# ---------------------------------------------------------------------- #
# 조회 헬퍼 — `camera_live_pi.py`와 `eval_video_recall.py`가 같은 함수를 쓴다
# (로직 복붙 금지 원칙: 배포 코드가 바뀌면 평가도 같이 따라가야 한다).
# ---------------------------------------------------------------------- #

def latched_cane_ids(entities: list[PedestrianEntity]) -> set[int]:
    """래치된 엔티티가 이번 프레임에 쥐고 있는 지팡이 track_id 집합.

    사람 동반 게이트를 **완화**하는 데 쓴다 — 기존 프레임 단위 판정과 `OR`로
    묶이므로 지금 통과하던 지팡이는 전부 계속 통과한다(하위호환).

    가상 박스와 달리 여기에는 시간 상한이 없다. **이번 프레임에 실제 지팡이 트랙이
    짝지어져 있어야** 값이 나오므로, 완화되는 것은 "지팡이는 보이는데 이번 프레임만
    사람 연관이 끊긴" 경우뿐이고 무한정 열리지 않는다.
    """
    return {e.cane_id for e in entities if e.is_cane_user and e.cane_id is not None}


def cane_user_person_ids(entities: list[PedestrianEntity]) -> set[int]:
    """래치된 지팡이 사용자의 **사람** track_id 집합 (유동인구 집계용)."""
    return {e.person_id for e in entities if e.is_cane_user}


def subject_for_canes(entities: list[PedestrianEntity],
                      tracks: list[dict]) -> dict[int, int]:
    """지팡이 track_id -> **안내 주체**(entity_id).

    ROI 안내를 사람 단위로 발사하려면 지팡이 박스가 누구 것인지 알아야 한다
    (`audio_trigger.StandaloneDispatcher.update()`의 subject).

    **1:1 배정 결과를 그대로 쓰면 안 된다.** 배정은 래치를 위한 것이라 사람 한 명당
    지팡이 하나만 묶는데, 탐지기는 같은 지팡이를 여러 박스로 내놓는 일이 흔하다 —
    test1 실측에서 통과한 지팡이 박스 중 **1,078프레임분이 배정에서 밀렸고, 그 전부가
    사람 후보를 가지고 있었다.** 그것들을 트랙 id로 폴백시키면 한 사람이 15개 주체로
    쪼개져 같은 사람에게 안내가 반복된다(실측 안내 1회 → 3회).

    그래서 **래치는 엄격한 1:1, 주체 식별은 느슨한 연관**으로 나눈다. 1:1은 옆 사람이
    덩달아 지팡이 사용자로 래치되는 것을 막는 데 꼭 필요하지만, "이 안내는 누구 것인가"는
    가까운 사람에게 붙이면 충분하다.

    사람 후보가 아예 없는 지팡이는 여기에 들어오지 않는다 — 호출부가 트랙 id로
    폴백한다(사람 동반 게이트를 끈 경우에만 생기는 경로다).
    """
    owner = {e.cane_id: e.entity_id for e in entities if e.cane_id is not None}
    person_entity = {e.person_id: e.entity_id for e in entities}

    _canes, _people, cands = candidate_pairs(tracks)
    # 가까운 짝부터 채워, 배정에서 밀린 지팡이도 가장 그럴듯한 사람에게 붙인다.
    for pid, cid, gap, dist in sorted(cands, key=lambda c: (c[2], c[3])):
        if cid not in owner and pid in person_entity:
            owner[cid] = person_entity[pid]
    return owner


def virtual_cane_boxes(entities: list[PedestrianEntity]) -> list[tuple[int, list]]:
    """(entity_id, 가상 지팡이 bbox) 목록 — 지팡이 트랙이 끊긴 래치 엔티티들."""
    return [(e.entity_id, e.virtual_cane_bbox)
            for e in entities if e.virtual_cane_bbox is not None]
