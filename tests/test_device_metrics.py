"""device_metrics · device_status 단위 테스트.

탐지 프로세스와 roi_editor가 sqlite로 값을 주고받는 경로, 그리고 /proc 읽기.
"""
import time

from device_metrics import read_metrics, report
from device_status import CpuSampler, read_status


def test_report_and_read(tmp_path):
    db = tmp_path / "m.db"
    report(db, "cam0", streaming=True, infer_ms=48.2, loop_ms=78.5, fps=12.8)
    (m,) = read_metrics(db)
    assert m["camera_id"] == "cam0"
    assert m["streaming"] is True
    assert m["infer_ms"] == 48.2 and m["fps"] == 12.8
    assert m["stale"] is False


def test_one_row_per_camera(tmp_path):
    """카메라당 1행 UPSERT — 프레임마다 행이 쌓이면 db가 터진다."""
    db = tmp_path / "m.db"
    for fps in (10.0, 11.0, 12.0):
        report(db, "cam0", streaming=True, fps=fps)
    rows = read_metrics(db)
    assert len(rows) == 1 and rows[0]["fps"] == 12.0


def test_two_cameras_are_separate(tmp_path):
    db = tmp_path / "m.db"
    report(db, "cam0", streaming=True, fps=12.0)
    report(db, "cam1", streaming=False, fps=0.0)
    assert {m["camera_id"]: m["streaming"] for m in read_metrics(db)} == {
        "cam0": True, "cam1": False}


def test_stale_report_is_reported_as_not_streaming(tmp_path):
    """탐지 프로세스가 죽었는데 마지막 값이 '정상'으로 남으면 서버에 거짓말을 한다."""
    db = tmp_path / "m.db"
    t0 = time.time()
    report(db, "cam0", streaming=True, infer_ms=50.0, fps=12.0, now=t0)
    (m,) = read_metrics(db, stale_after=10.0, now=t0 + 30.0)
    assert m["stale"] is True
    assert m["streaming"] is False
    assert m["infer_ms"] is None and m["fps"] is None
    assert m["age_sec"] == 30.0


def test_report_never_raises(tmp_path):
    """탐지 루프에서 호출된다 — sqlite 오류로 안내가 멈추면 안 된다."""
    report(tmp_path / "없는디렉터리" / "m.db", "cam0", streaming=True)


def test_read_metrics_on_missing_db(tmp_path):
    assert read_metrics(tmp_path / "없음.db") == []


# --------------------------------------------------------------------- #
# device_status
# --------------------------------------------------------------------- #

def test_read_status_returns_all_keys():
    """Pi가 아닌 환경에서는 일부가 None이지만 키는 항상 있어야 한다."""
    st = read_status()
    assert set(st) == {"uptime_seconds", "cpu_temp_c", "load_avg",
                       "mem_used_mb", "mem_total_mb"}


def test_cpu_sampler_needs_two_samples():
    """/proc/stat은 부팅 이후 누적값이라 한 번 읽어서는 사용률을 알 수 없다."""
    s = CpuSampler()
    assert s.sample() is None          # 첫 호출은 기준점만 잡는다
    second = s.sample()
    assert second is None or 0.0 <= second <= 100.0
