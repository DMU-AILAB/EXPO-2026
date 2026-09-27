"""label_tool/server.py 단위 테스트 — cane_only 스캔, 저장 시 기존 라벨 보존, reviewed 추적.

fastapi/httpx는 roi_editor와 마찬가지로 선택 의존성이라 없으면 이 파일 전체를 건너뛴다.
roi_editor/server.py도 모듈명이 똑같이 "server"라서 단순 sys.path 삽입 + import server로
불러오면 sys.modules 캐시가 먼저 로드된 쪽을 재사용해버려 테스트 실행 순서에 따라
AttributeError가 난다 — importlib로 파일 경로 기준 고유한 이름의 모듈로 직접 로드해 피한다.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

_spec = importlib.util.spec_from_file_location(
    "label_tool_server", Path(__file__).parent.parent / "apps" / "label_tool" / "server.py"
)
srv = importlib.util.module_from_spec(_spec)
sys.modules["label_tool_server"] = srv
_spec.loader.exec_module(srv)


def _make_dataset(tmp_path):
    """train에 cane_only 1장 + both-labeled 1장(대상 제외), val에 cane_only 1장을 만든다."""
    for split, name, label in [
        ("train", "img1.jpg", "0 0.5 0.5 0.2 0.3\n"),
        ("train", "img2.jpg", "0 0.1 0.1 0.05 0.05\n1 0.6 0.6 0.1 0.1\n"),  # 이미 사람도 있음 -> 대상 제외
        ("val",   "img3.jpg", "0 0.3 0.3 0.1 0.1\n"),
    ]:
        img_dir = tmp_path / split / "images"
        lbl_dir = tmp_path / split / "labels"
        img_dir.mkdir(parents=True, exist_ok=True)
        lbl_dir.mkdir(parents=True, exist_ok=True)
        (img_dir / name).write_bytes(b"\xff\xd8\xff")  # 내용은 중요하지 않음(존재 여부만 확인)
        (lbl_dir / (Path(name).stem + ".txt")).write_text(label, encoding="utf-8")


@pytest.fixture
def client(tmp_path, monkeypatch):
    """스캔/저장 로직을 검증하는 기본 픽스처.

    `allowed_splits`를 세 split 전부로 열어 둔다 — 이 파일의 원래 테스트들은 **split을
    넘나드는 스캔**을 검증하는 것이 목적이고, 기본값(train 전용)은 별도 테스트로 고정한다.
    `auto_suggest`는 끈다: 켜두면 아이템을 만들 때마다 탐지 모델을 부르게 되어 테스트가
    느려지고 외부 가중치 유무에 따라 결과가 흔들린다.
    """
    _make_dataset(tmp_path)
    monkeypatch.setattr(srv, "datasets_dir", tmp_path)
    monkeypatch.setattr(srv, "reviewed_path", tmp_path / "reviewed.json")
    monkeypatch.setattr(srv, "target_mode", "cane_only")
    monkeypatch.setattr(srv, "allowed_splits", ["train", "val", "test"])
    monkeypatch.setattr(srv, "auto_suggest", False)
    srv._reset_cache()
    return TestClient(srv.app)


def test_stats_finds_only_cane_only_images(client):
    res = client.get("/api/stats")
    assert res.status_code == 200
    assert res.json() == {"total": 2, "reviewed": 0, "remaining": 2}


def test_next_returns_first_cane_only_item(client):
    res = client.get("/api/next?after_index=-1")
    body = res.json()
    assert body["done"] is False
    assert body["split"] == "train"
    assert body["filename"] == "img1.jpg"
    assert body["cane_boxes"] == [[0.5, 0.5, 0.2, 0.3]]
    assert body["person_boxes"] == []


def test_save_appends_person_box_and_preserves_existing_cane_line(client, tmp_path):
    res = client.post("/api/save", json={
        "split": "train", "filename": "img1.jpg",
        "person_boxes": [[0.4, 0.4, 0.1, 0.1]],
    })
    assert res.status_code == 200
    assert res.json() == {"ok": True, "person_count": 1}

    label_text = (tmp_path / "train" / "labels" / "img1.txt").read_text()
    lines = label_text.strip().splitlines()
    assert lines[0].startswith("0 0.500000 0.500000 0.200000 0.300000")
    assert lines[1].startswith("1 0.400000 0.400000 0.100000 0.100000")


def test_save_marks_reviewed_and_advances_next(client):
    client.post("/api/save", json={"split": "train", "filename": "img1.jpg", "person_boxes": []})
    stats = client.get("/api/stats").json()
    assert stats == {"total": 2, "reviewed": 1, "remaining": 1}

    nxt = client.get("/api/next?after_index=-1").json()
    assert (nxt["split"], nxt["filename"]) == ("val", "img3.jpg")


def test_all_reviewed_reports_done(client):
    client.post("/api/save", json={"split": "train", "filename": "img1.jpg", "person_boxes": []})
    client.post("/api/save", json={"split": "val", "filename": "img3.jpg", "person_boxes": []})
    res = client.get("/api/next?after_index=-1")
    assert res.json()["done"] is True


def test_item_out_of_range_returns_404(client):
    res = client.get("/api/item/99")
    assert res.status_code == 404


def test_suggest_defaults_to_local_yolo(client, monkeypatch):
    monkeypatch.setattr(srv, "_call_local_yolo_suggest", lambda path: [[0.5, 0.5, 0.2, 0.4]])
    res = client.post("/api/suggest/0")
    assert res.status_code == 200
    assert res.json() == {"boxes": [[0.5, 0.5, 0.2, 0.4]], "provider": "local_yolo"}


def test_suggest_openai_provider_returns_boxes(client, monkeypatch):
    monkeypatch.setattr(srv, "_call_openai_suggest", lambda path: [[0.5, 0.5, 0.2, 0.4]])
    res = client.post("/api/suggest/0?provider=openai")
    assert res.status_code == 200
    assert res.json() == {"boxes": [[0.5, 0.5, 0.2, 0.4]], "provider": "openai"}


def test_suggest_unknown_provider_returns_400(client):
    res = client.post("/api/suggest/0?provider=bogus")
    assert res.status_code == 400


def test_suggest_out_of_range_returns_404(client):
    res = client.post("/api/suggest/99")
    assert res.status_code == 404


def test_suggest_missing_api_key_returns_400(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    pytest.importorskip("openai")
    res = client.post("/api/suggest/0?provider=openai")
    assert res.status_code == 400
    assert "OPENAI_API_KEY" in res.json()["detail"]


def test_suggest_never_writes_label_file(client, monkeypatch, tmp_path):
    """제안 API는 저장하지 않는다 — /api/save를 명시적으로 눌러야만 라벨이 바뀐다."""
    monkeypatch.setattr(srv, "_call_local_yolo_suggest", lambda path: [[0.5, 0.5, 0.2, 0.4]])
    before = (tmp_path / "train" / "labels" / "img1.txt").read_text()
    client.post("/api/suggest/0")
    after = (tmp_path / "train" / "labels" / "img1.txt").read_text()
    assert before == after


def test_save_unknown_file_returns_404(client):
    res = client.post("/api/save", json={
        "split": "train", "filename": "nonexistent.jpg", "person_boxes": [],
    })
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# 대기열 방식 · 편집 가드 · 자동 제안 선탑재 (2026-09-21 확장)
# ---------------------------------------------------------------------------

def test_기본값은_train만_편집_대상이다():
    """**잣대 보호가 기본이어야 한다.**

    val/test 라벨을 고치면 리포트 §13~§15의 지표와 비교가 깨진다 — 같은 데이터로
    잰 값이 아니게 되기 때문이다. 실수로 열리지 않도록 기본값을 코드에 고정한다.
    """
    assert srv.allowed_splits == ["train"]


def test_허용되지_않은_split_저장은_403(tmp_path, monkeypatch):
    _make_dataset(tmp_path)
    monkeypatch.setattr(srv, "datasets_dir", tmp_path)
    monkeypatch.setattr(srv, "reviewed_path", tmp_path / "reviewed.json")
    monkeypatch.setattr(srv, "allowed_splits", ["train"])
    monkeypatch.setattr(srv, "auto_suggest", False)
    srv._reset_cache()
    c = TestClient(srv.app)

    res = c.post("/api/save", json={"split": "val", "filename": "img3.jpg", "person_boxes": []})
    assert res.status_code == 403
    # 거부됐으니 파일이 그대로여야 한다
    assert (tmp_path / "val" / "labels" / "img3.txt").read_text() == "0 0.3 0.3 0.1 0.1\n"


def test_all_모드는_사람이_이미_있는_이미지도_포함한다(tmp_path, monkeypatch):
    """cane_only 모드가 건너뛰던 img2(지팡이+사람)가 대기열에 들어와야 한다."""
    _make_dataset(tmp_path)
    monkeypatch.setattr(srv, "datasets_dir", tmp_path)
    monkeypatch.setattr(srv, "reviewed_path", tmp_path / "reviewed.json")
    monkeypatch.setattr(srv, "target_mode", "all")
    monkeypatch.setattr(srv, "allowed_splits", ["train"])
    monkeypatch.setattr(srv, "auto_suggest", False)
    srv._reset_cache()
    c = TestClient(srv.app)

    assert c.get("/api/stats").json()["total"] == 2          # img1 + img2
    names = {c.get(f"/api/item/{i}").json()["filename"] for i in range(2)}
    assert names == {"img1.jpg", "img2.jpg"}


def test_queue_모드는_지정한_순서를_따른다(tmp_path, monkeypatch):
    """감사 결과가 정한 우선순위(누락이 많은 순 등)를 그대로 작업 순서로 쓴다."""
    import json as _json
    _make_dataset(tmp_path)
    q = tmp_path / "q.json"
    q.write_text(_json.dumps([
        {"img": "img2.jpg", "split": "train"},
        {"img": "img1.jpg", "split": "train"},
        {"img": "img3.jpg", "split": "val"},      # 허용되지 않은 split → 제외
        {"img": "img2.jpg", "split": "train"},    # 중복 → 한 번만
        {"img": "없는파일.jpg", "split": "train"},  # 존재하지 않음 → 제외
    ]), encoding="utf-8")
    monkeypatch.setattr(srv, "datasets_dir", tmp_path)
    monkeypatch.setattr(srv, "reviewed_path", tmp_path / "reviewed.json")
    monkeypatch.setattr(srv, "target_mode", "queue")
    monkeypatch.setattr(srv, "queue_path", q)
    monkeypatch.setattr(srv, "allowed_splits", ["train"])
    monkeypatch.setattr(srv, "auto_suggest", False)
    srv._reset_cache()
    c = TestClient(srv.app)

    assert c.get("/api/stats").json()["total"] == 2
    assert c.get("/api/item/0").json()["filename"] == "img2.jpg"
    assert c.get("/api/item/1").json()["filename"] == "img1.jpg"


def test_자동제안은_기존_라벨과_겹치는_것을_뺀다(tmp_path, monkeypatch):
    """**겹침 제거가 이 기능의 핵심이다.**

    일부만 라벨된 이미지에서 제안을 그대로 얹으면 이미 있는 사람 위에 박스가 중복으로
    쌓여, 사람이 무엇을 확인해야 하는지 알 수 없게 된다. 빠진 것만 떠야 한다.
    """
    _make_dataset(tmp_path)
    monkeypatch.setattr(srv, "datasets_dir", tmp_path)
    monkeypatch.setattr(srv, "reviewed_path", tmp_path / "reviewed.json")
    monkeypatch.setattr(srv, "target_mode", "all")
    monkeypatch.setattr(srv, "allowed_splits", ["train"])
    monkeypatch.setattr(srv, "auto_suggest", True)
    # img2의 기존 사람 라벨은 [0.6, 0.6, 0.1, 0.1]
    monkeypatch.setattr(srv, "_call_local_yolo_suggest",
                        lambda path: [[0.6, 0.6, 0.1, 0.1],      # 기존과 동일 → 빠져야 함
                                      [0.2, 0.8, 0.1, 0.1]])     # 새로운 것 → 남아야 함
    srv._reset_cache()
    c = TestClient(srv.app)

    item = next(c.get(f"/api/item/{i}").json() for i in range(2)
                if c.get(f"/api/item/{i}").json()["filename"] == "img2.jpg")
    assert item["person_boxes"] == [[0.6, 0.6, 0.1, 0.1]]
    assert item["auto_boxes"] == [[0.2, 0.8, 0.1, 0.1]]


def test_검수완료된_이미지는_제안을_다시_얹지_않는다(tmp_path, monkeypatch):
    """되돌리기 방지 — 사람이 지웠던 제안이 다시 열 때 되살아나면 작업이 무효가 된다."""
    _make_dataset(tmp_path)
    monkeypatch.setattr(srv, "datasets_dir", tmp_path)
    monkeypatch.setattr(srv, "reviewed_path", tmp_path / "reviewed.json")
    monkeypatch.setattr(srv, "target_mode", "all")
    monkeypatch.setattr(srv, "allowed_splits", ["train"])
    monkeypatch.setattr(srv, "auto_suggest", True)
    monkeypatch.setattr(srv, "_call_local_yolo_suggest", lambda path: [[0.2, 0.8, 0.1, 0.1]])
    srv._reset_cache()
    c = TestClient(srv.app)

    c.post("/api/save", json={"split": "train", "filename": "img1.jpg", "person_boxes": []})
    idx = next(i for i in range(2) if c.get(f"/api/item/{i}").json()["filename"] == "img1.jpg")
    assert c.get(f"/api/item/{idx}").json()["auto_boxes"] == []


def test_cane_boxes를_보내면_지팡이_라벨도_교체된다(client, tmp_path):
    res = client.post("/api/save", json={
        "split": "train", "filename": "img1.jpg",
        "person_boxes": [[0.4, 0.4, 0.1, 0.1]],
        "cane_boxes": [[0.11, 0.22, 0.33, 0.44]],
    })
    assert res.status_code == 200
    lines = (tmp_path / "train" / "labels" / "img1.txt").read_text().strip().splitlines()
    assert lines[0].startswith("0 0.110000 0.220000 0.330000 0.440000")
    assert lines[1].startswith("1 0.400000 0.400000 0.100000 0.100000")


def test_cane_boxes를_빈_목록으로_보내면_지팡이가_지워진다(client, tmp_path):
    """'미전달'과 '빈 목록'은 다르다 — 빈 목록은 '지팡이가 없다'는 명시적 판단이다."""
    res = client.post("/api/save", json={
        "split": "train", "filename": "img1.jpg",
        "person_boxes": [], "cane_boxes": [],
    })
    assert res.status_code == 200
    assert (tmp_path / "train" / "labels" / "img1.txt").read_text().strip() == ""
