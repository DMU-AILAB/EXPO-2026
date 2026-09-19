"""gate_chain.py — 탐지 결과를 트리거 판정까지 끌고 가는 **단일 구현**.

이 프로젝트에는 같은 게이트 체인을 돌려야 하는 곳이 셋이다.

| 쓰는 곳 | 무엇을 위해 |
|---|---|
| `camera_live_pi.py` | 실제 배포 — 카메라 프레임 |
| `tools/eval/eval_video_recall.py` | 모델 채택 판정 — 저장된 추론 결과 |
| `replay_engine.py` (roi_editor 검증 탭) | 영상을 화면에서 눈으로 확인 |

셋이 각자 로직을 들고 있으면 **배포 코드가 바뀔 때 나머지가 조용히 어긋난다** —
평가가 실제와 다른 것을 재고, 화면이 실제와 다른 것을 보여준다. 그래서 순서·상수·
연관 로직을 여기 한 곳에만 둔다.

체인의 순서는 의미가 있다. 각 게이트가 서로 다른 오탐지 유형을 막으므로 순서를
바꾸면 판정이 달라진다.

    정지 억제  →  움직임 게이트  →  [엔티티 갱신]  →  사람 동반(래치로 완화)

엔티티 갱신이 **움직임 게이트 뒤, 사람 동반 게이트 앞**에 오는 것이 핵심이다.
래치의 입력은 앞의 두 게이트를 통과한 지팡이이고 출력은 사람 동반 게이트의 완화라,
순서를 바꾸면 순환이 생긴다(`pedestrian_entity` 모듈 docstring 참고).

표준 라이브러리만 쓴다 — `simple_tracker`·`cane_person_assoc`·`pedestrian_entity`와
같은 원칙이다(Pi 배포 대상). numpy·cv2를 import하지 말 것.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cane_person_assoc import CANE_CLASS_ID, PERSON_CLASS_ID, associate_canes
from pedestrian_entity import (
    EntityTracker,
    PedestrianEntity,
    latched_cane_ids,
    subject_for_canes,
    virtual_cane_boxes,
)
from simple_tracker import SimpleTracker

__all__ = [
    "STATIC_CANE_SUPPRESS_FRAMES",
    "MOVED_MIN_DIAG_RATIO",
    "GateResult",
    "GateChain",
]

# 지팡이 트랙이 이만큼 연속으로 거의 안 움직이면(SimpleTracker.static_frames)
# 배경 오탐지(케이블/문틀 경계선 등)로 간주해 ROI 트리거 대상에서 제외한다.
# 실측 FPS(~8~9)에서 대략 3초 정도에 해당 — 사람이 잠시 멈춰 서서 지팡이를
# 짚고 있는 정상적인 상황보다는 넉넉하게 잡았다.
STATIC_CANE_SUPPRESS_FRAMES = 24

# 지팡이 트랙이 트리거 자격을 얻으려면 생성 지점 대비 이 비율(프레임 대각선 기준)만큼
# 움직인 적이 있어야 한다. 픽셀 절대값이 아니라 비율인 이유는 회전(90/270)으로 가로세로가
# 바뀌어도 같은 기준이 유지되어야 하기 때문이다. 640x480이면 약 16px로, 트래커의 지터
# 임계값(static_move_px=3.0)의 5배 여유가 있다 — 실측상 고정 물체는 300프레임 뒤에도
# 원점 대비 4px를 넘지 않고, 이동하는 물체는 30프레임 만에 100px를 넘는다.
MOVED_MIN_DIAG_RATIO = 0.02


@dataclass
class GateResult:
    """한 프레임의 판정 결과 — 호출부가 필요한 것을 골라 쓴다."""

    tracks: list[dict]                        # 이번 프레임의 전체 트랙
    all_cane_tracks: list[dict]               # 지팡이 트랙 전부(게이트 통과 여부 무관)
    cane_tracks: list[dict]                   # 3중 게이트를 모두 통과한 지팡이
    person_tracks: list[dict]
    entities: list[PedestrianEntity]
    latched_ids: set[int]                     # 래치로 사람 동반 게이트를 통과한 지팡이
    virtual: list[tuple[int, list]]           # (entity_id, 가상 지팡이 bbox)
    with_person: dict[int, bool]              # 프레임 단위 사람 동반 판정(디버그 표시용)
    moved_min: float                          # 이번 프레임의 움직임 게이트 임계값(px)
    roi_targets: list[tuple[object, list]] = field(default_factory=list)

    @property
    def passing(self) -> bool:
        """ROI 판정 대상이 하나라도 있는가 — 평가에서 '통과 프레임'의 정의."""
        return bool(self.cane_tracks or self.virtual)


class GateChain:
    """트래커 + 엔티티 + 3중 게이트를 묶어 프레임 단위로 돌린다.

    상태(트래커·엔티티)를 들고 있으므로 **카메라/영상 하나당 하나씩** 만들어야 한다.
    """

    def __init__(self, require_person: bool = True,
                 tracker: SimpleTracker | None = None,
                 entity_tracker: EntityTracker | None = None,
                 use_entity: bool = True,
                 static_suppress_frames: int = STATIC_CANE_SUPPRESS_FRAMES,
                 moved_min_diag_ratio: float = MOVED_MIN_DIAG_RATIO) -> None:
        self.require_person = require_person
        self.tracker = tracker if tracker is not None else SimpleTracker()
        # `use_entity=False`는 평가에서 엔티티 레이어의 A/B 기준선을 뽑기 위한 것이다.
        self.entity_tracker = (entity_tracker if entity_tracker is not None
                               else EntityTracker()) if use_entity else None
        self.static_suppress_frames = static_suppress_frames
        self.moved_min_diag_ratio = moved_min_diag_ratio

    def step(self, dets: list[dict], frame_shape: tuple[int, int],
             now: float) -> GateResult:
        """탐지 결과 한 프레임을 받아 판정한다.

        `frame_shape`는 `(높이, 너비)`다 — 움직임 게이트 임계값이 프레임 대각선
        비율이라 필요하다. `now`는 초 단위 단조 시각(재식별·래치가 초 단위로 센다).
        """
        h, w = frame_shape
        tracks = self.tracker.update(dets, now)

        all_cane = [t for t in tracks if t["class"] == CANE_CLASS_ID]
        person_tracks = [t for t in tracks if t["class"] == PERSON_CLASS_ID]

        # 1. 정지 억제 — 배경의 케이블/문틀 경계선처럼 절대 안 움직이는 것을 막는다.
        cane = [t for t in all_cane
                if t.get("static_frames", 0) < self.static_suppress_frames]

        # 2. 움직임 게이트 — 정지 억제에는 약 2초의 공백이 있다(새 트랙은
        #    static_frames가 0에서 시작). "정지가 증명되기 전까지 통과"를 "움직임이
        #    증명되기 전까지 억제"로 뒤집어 그 공백을 닫는다. 사람이 동반돼도
        #    면제하지 않는다 — 사람 발치의 기둥/난간이 정확히 그 유형이다.
        moved_min = ((w ** 2 + h ** 2) ** 0.5) * self.moved_min_diag_ratio
        if cane:
            cane = [t for t in cane if t.get("max_disp", 0.0) >= moved_min]

        # 3. 엔티티 갱신 — 입력은 **여기까지 통과한** 지팡이다. 사람 동반 게이트의
        #    결과를 넣으면 순환이 생긴다(모듈 docstring 참고).
        entities: list[PedestrianEntity] = []
        latched: set[int] = set()
        virtual: list[tuple[int, list]] = []
        if self.entity_tracker is not None:
            entities = self.entity_tracker.update(
                tracks, {t["track_id"] for t in cane}, now)
            latched = latched_cane_ids(entities)
            virtual = virtual_cane_boxes(entities)

        # 4. 사람 동반 — 프레임 단위 판정 **또는** 래치. OR이므로 기존에 통과하던
        #    지팡이는 전부 계속 통과한다(단조 완화).
        with_person: dict[int, bool] = {}
        if self.require_person and cane:
            with_person = associate_canes(tracks)
            cane = [t for t in cane
                    if with_person.get(t["track_id"], False)
                    or t["track_id"] in latched]

        # ROI 판정 대상 = (주체, 박스). 주체는 사람 한 명(entity_id)이며, 사람 후보가
        # 없는 지팡이만 트랙 id로 폴백한다(사람 동반 게이트를 끈 경우에만 생긴다).
        owner = subject_for_canes(entities, tracks) if entities else {}
        roi_targets: list[tuple[object, list]] = [
            (owner.get(t["track_id"], ("cane", t["track_id"])), t["bbox"])
            for t in cane
        ]
        roi_targets += [(eid, bbox) for eid, bbox in virtual]

        return GateResult(
            tracks=tracks, all_cane_tracks=all_cane, cane_tracks=cane,
            person_tracks=person_tracks, entities=entities, latched_ids=latched,
            virtual=virtual, with_person=with_person, moved_min=moved_min,
            roi_targets=roi_targets,
        )

    def debug_with_person(self, tracks: list[dict]) -> dict[int, bool]:
        """디버그 오버레이 전용 — 통과한 지팡이가 없어도 사유를 표시하려면 필요하다.

        `step()`은 지팡이가 없으면 연관 계산을 건너뛴다(불필요한 비용). 화면에
        "왜 막혔는지"를 그리려면 그때도 값이 있어야 해서 따로 연다.
        """
        return associate_canes(tracks) if self.require_person else {}
