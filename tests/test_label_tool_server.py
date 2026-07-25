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
    "label_tool_server", Path(__file__).parent.parent / "label_tool" / "server.py"
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
    _make_dataset(tmp_path)
    monkeypatch.setattr(srv, "datasets_dir", tmp_path)
    monkeypatch.setattr(srv, "reviewed_path", tmp_path / "reviewed.json")
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
