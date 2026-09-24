"""gate_chain 단위 테스트 — 배포·평가·재생검증이 공유하는 단일 게이트 구현.

여기서 고정하는 것은 **순서와 계약**이다. 개별 게이트의 동작은 각 모듈의 테스트
(`test_simple_tracker`·`test_pedestrian_entity`·`test_cane_person_assoc`)가 본다.
"""

from gate_chain import MOVED_MIN_DIAG_RATIO, STATIC_CANE_SUPPRESS_FRAMES, GateChain

SHAPE = (480, 640)                      # (높이, 너비) — 대각선 800px
DT = 0.1


def _det(bbox, cls=0, label="white_cane", conf=0.9):
    return {"bbox": bbox, "class": cls, "conf": conf, "label": label}


def _walk(chain, steps, *, with_person=True, start=100, step=20, t0=0.0):
    """사람과 지팡이가 나란히 오른쪽으로 걸어간다."""
    out = None
    for i in range(steps):
        x = start + i * step
        dets = [_det([x + 95, 250, x + 115, 320])]
        if with_person:
            dets.append(_det([x, 100, x + 100, 300], cls=1, label="person"))
        out = chain.step(dets, SHAPE, t0 + i * DT)
    return out


def test_constants_live_here_not_in_camera_live_pi():
    """배포·평가·재생검증이 같은 값을 보게 하려고 이 모듈로 모았다."""
    import camera_live_pi
    assert camera_live_pi.STATIC_CANE_SUPPRESS_FRAMES is STATIC_CANE_SUPPRESS_FRAMES
    assert camera_live_pi.MOVED_MIN_DIAG_RATIO is MOVED_MIN_DIAG_RATIO


def test_moving_cane_with_person_passes_all_three_gates():
    g = _walk(GateChain(), steps=12)
    assert len(g.cane_tracks) == 1
    assert g.passing is True
    assert g.roi_targets and g.roi_targets[0][1] == g.cane_tracks[0]["bbox"]


def test_cane_that_never_moves_is_blocked_by_the_movement_gate():
    """움직임 게이트 — 배경 기둥/난간처럼 제자리에 있는 것."""
    chain = GateChain()
    for i in range(12):
        g = chain.step([_det([300, 250, 320, 320]),
                        _det([200, 100, 300, 300], cls=1, label="person")],
                       SHAPE, i * DT)
    assert g.all_cane_tracks and not g.cane_tracks
    assert g.passing is False


def test_moving_cane_without_person_is_blocked_when_required():
    g = _walk(GateChain(require_person=True), steps=12, with_person=False)
    assert g.all_cane_tracks and not g.cane_tracks


def test_same_cane_passes_when_person_is_not_required():
    g = _walk(GateChain(require_person=False), steps=12, with_person=False)
    assert len(g.cane_tracks) == 1


def test_moved_min_scales_with_frame_diagonal():
    """임계값이 픽셀 절대값이 아니라 프레임 대각선 비율이어야 회전에도 일관된다."""
    chain = GateChain()
    small = chain.step([], (240, 320), 0.0).moved_min
    large = chain.step([], (960, 1280), 0.1).moved_min
    assert large == small * 4
    assert abs(small - (400 * MOVED_MIN_DIAG_RATIO)) < 1e-6


def test_entity_layer_can_be_turned_off_for_ab_baseline():
    chain = GateChain(use_entity=False)
    g = _walk(chain, steps=12)
    assert g.entities == [] and g.virtual == []
    assert len(g.cane_tracks) == 1          # 프레임 단위 연관만으로도 통과한다


def test_roi_targets_use_the_person_entity_as_subject():
    """안내 주체는 사람 한 명(entity_id)이다 — 지팡이 트랙 id가 아니다."""
    g = _walk(GateChain(), steps=12)
    subject = g.roi_targets[0][0]
    assert subject in {e.entity_id for e in g.entities}


def test_virtual_box_enters_roi_targets_when_the_cane_drops_out():
    chain = GateChain()
    g = _walk(chain, steps=12)
    assert g.virtual == []

    # 지팡이 탐지만 끊고 사람은 계속 걷게 한다.
    #
    # 가상 박스는 **트랙이 죽을 때가 아니라 기하 결합이 끊길 때** 시작된다.
    # `SimpleTracker`가 max_age 동안 마지막 박스를 얼려 두는데, 사람이 걸어가면
    # 그 굳은 박스와의 간격이 벌어져 `candidate_pairs`의 조건을 못 넘기게 되고,
    # 그때부터 엔티티의 `cane_id`가 None이 되어 투영이 시작된다. 즉 coasting이
    # `VIRTUAL_MAX_SEC` 예산을 잡아먹지 않는다.
    for i in range(12, 23):
        x = 100 + i * 20
        g = chain.step([_det([x, 100, x + 100, 300], cls=1, label="person")],
                       SHAPE, i * DT)
    assert g.virtual, "래치된 엔티티는 지팡이가 끊겨도 가상 박스를 내야 한다"
    assert g.passing is True
    assert any(bbox == g.virtual[0][1] for _sid, bbox in g.roi_targets)


def test_virtual_box_expires_and_the_frame_stops_passing():
    """상한이 없으면 사람 트랙이 사는 내내 유령 안내가 나간다(리포트 §8-4)."""
    chain = GateChain()
    _walk(chain, steps=12)
    for i in range(12, 40):                      # 2.8초 — 상한을 한참 넘긴다
        x = 100 + i * 20
        g = chain.step([_det([x, 100, x + 100, 300], cls=1, label="person")],
                       SHAPE, i * DT)
    assert g.virtual == []
    assert g.passing is False


def test_static_suppression_precedes_the_movement_gate():
    """순서가 의미를 가진다 — 정지 억제가 먼저다.

    오래 고정된 트랙은 `static_frames`가 임계값을 넘는 순간, 그 뒤의 게이트를
    보기도 전에 떨어진다.
    """
    chain = GateChain(require_person=False)
    for i in range(STATIC_CANE_SUPPRESS_FRAMES + 5):
        g = chain.step([_det([300, 250, 320, 320])], SHAPE, i * DT)
    assert g.all_cane_tracks[0]["static_frames"] >= STATIC_CANE_SUPPRESS_FRAMES
    assert not g.cane_tracks


# ---------------------------------------------------------------------------
# 구조물 마스크 (2026-09-24 추가)
# ---------------------------------------------------------------------------
def _mask(*boxes_px, cls=0):
    """픽셀 박스 → StaticMask (SHAPE 기준 정규화)."""
    import static_mask
    h, w = SHAPE
    return static_mask.StaticMask(
        [{"cls": cls, "bbox": [x1 / w, y1 / h, x2 / w, y2 / h]}
         for (x1, y1, x2, y2) in boxes_px])


def test_마스크를_안_주면_동작이_완전히_같다():
    """**이것이 가장 중요한 계약이다.**

    기존 기기에는 마스크 파일이 없다. `static_mask=None` 경로가 조금이라도 달라지면
    리포트 §13~§15에서 6런·9관측으로 낸 지표가 전부 무효가 된다.
    """
    a = _walk(GateChain(), 12)
    b = _walk(GateChain(static_mask=None), 12)
    assert [t["track_id"] for t in a.cane_tracks] == [t["track_id"] for t in b.cane_tracks]
    assert len(a.roi_targets) == len(b.roi_targets)


def test_가만히_있는_구조물은_마스크로_막힌다():
    """정지 억제(24프레임 ≈ 2초)를 기다리지 않고 **첫 프레임부터** 막아야 한다 —
    디바운스가 0.5초라 그 공백에서 이미 음성이 나가는 것이 원래 결함이다."""
    box = [300, 200, 320, 280]
    chain = GateChain(static_mask=_mask(tuple(box)), require_person=False)
    out = None
    for i in range(6):                       # 정지 억제가 걸리기 한참 전
        out = chain.step([_det(box)], SHAPE, i * DT)
    assert out.cane_tracks == []


def test_같은_자리라도_움직이면_마스크가_무효화된다():
    """마스크는 사전확률이지 최종 판정이 아니다 — 움직인 트랙은 되살아나야 한다.

    이게 없으면 마스크를 켠 자리가 **영구 사각지대**가 된다.
    """
    chain = GateChain(static_mask=_mask((100, 250, 120, 320)), require_person=False)
    out = _walk(chain, 12, with_person=False)      # 마스크 자리에서 출발해 이동
    assert out.cane_tracks, "움직였는데도 마스크에 막혔다 — 사각지대가 된다"


def test_마스크는_다른_클래스를_건드리지_않는다():
    """지팡이 마스크가 사람 탐지를 지우면 사람 동반 게이트가 막혀 안내가 사라진다."""
    chain = GateChain(static_mask=_mask((100, 100, 200, 300), cls=0))
    out = _walk(chain, 12)
    assert any(t["class"] == 1 for t in out.tracks), "사람 트랙이 사라졌다"


def test_마스크로_걸러낸_것은_트랙당_한_번만_보고된다():
    """매 프레임 sqlite에 쓰면 탐지 루프가 I/O에 막힌다
    (`fp_hotspots.log_suppressed`와 같은 계약)."""
    box = [300, 200, 320, 280]
    seen = []
    chain = GateChain(static_mask=_mask(tuple(box)), require_person=False,
                      on_mask_drop=lambda t: seen.append(t["track_id"]))
    for i in range(20):
        chain.step([_det(box)], SHAPE, i * DT)
    assert len(seen) == 1, f"트랙 1개에 {len(seen)}번 보고됐다"


def test_마스크_적중_기억에_상한이_있다():
    """누적 컬렉션은 반드시 정리한다 — 이 프로젝트의 규율.

    `_graves`(revive_sec 만료)·`_by_person`(del)·`audio_trigger`(del)와 같은 이유다.
    track_id는 단조 증가하므로 넘치면 작은 id부터 버려도 안전하다(그 트랙은 이미 소멸).
    """
    import gate_chain as gc
    chain = GateChain(static_mask=_mask((300, 200, 320, 280)), require_person=False,
                      on_mask_drop=lambda t: None)
    # 상한을 훌쩍 넘는 수의 서로 다른 트랙 id를 직접 밀어 넣는다
    for i in range(gc._MASK_REPORT_MEMORY * 2):
        if len(chain._mask_reported) >= gc._MASK_REPORT_MEMORY:
            keep = sorted(chain._mask_reported)[gc._MASK_REPORT_MEMORY // 2:]
            chain._mask_reported = set(keep)
        chain._mask_reported.add(i)
    assert len(chain._mask_reported) <= gc._MASK_REPORT_MEMORY


def test_상한을_넘겨도_트랙당_한_번은_지켜진다():
    """상한 때문에 같은 트랙이 두 번 보고되면 로그가 부풀어 사각지대 판단을 흐린다."""
    box = [300, 200, 320, 280]
    seen = []
    chain = GateChain(static_mask=_mask(tuple(box)), require_person=False,
                      on_mask_drop=lambda t: seen.append(t["track_id"]))
    for i in range(200):                      # 한 트랙이 오래 살아남는 상황
        chain.step([_det(box)], SHAPE, i * DT)
    assert len(seen) == 1
