"""프런트 정적 서빙 — 백엔드가 `dist`를 같은 포트로 내보낸다.

핵심은 SPA 폴백이 **API의 404를 가리지 않는 것**이다. 폴백이 `/api/...` 미존재까지
index.html(200)로 돌려주면 클라이언트가 오타 난 경로를 성공으로 오해한다.
`dist`가 없을 때는 개발 모드(`vite dev` + `uvicorn`) 그대로여야 한다.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.frontend_serve import mount_frontend


def _app(dist) -> FastAPI:
    app = FastAPI()

    @app.get("/api/ping")
    def ping():
        return {"ok": True}

    # main.py와 같은 배선: 마운트하지 못했을 때만 예전 상태 JSON을 `/`에 둔다.
    if not mount_frontend(app, dist):
        @app.get("/")
        def root_json():
            return {"status": "dev", "ok": True}

    return app


def _make_dist(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>SPA</html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    return dist


def test_dist가_없으면_아무것도_바꾸지_않는다(tmp_path):
    client = TestClient(_app(tmp_path / "nope"))
    assert client.get("/").json() == {"status": "dev", "ok": True}
    assert client.get("/devices").status_code == 404


def test_index_html이_없는_빈_dist도_비활성이다(tmp_path):
    (tmp_path / "dist").mkdir()
    client = TestClient(_app(tmp_path / "dist"))
    assert client.get("/").json()["status"] == "dev"


def test_루트는_index를_내보낸다(tmp_path):
    client = TestClient(_app(_make_dist(tmp_path)))
    r = client.get("/")
    assert r.status_code == 200 and "SPA" in r.text


def test_spa_경로는_index로_폴백한다(tmp_path):
    client = TestClient(_app(_make_dist(tmp_path)))
    for path in ("/devices", "/devices/pi-1/cameras/cam0", "/login"):
        r = client.get(path)
        assert r.status_code == 200 and "SPA" in r.text, path


def test_assets는_파일을_그대로_내보낸다(tmp_path):
    client = TestClient(_app(_make_dist(tmp_path)))
    r = client.get("/assets/app.js")
    assert r.status_code == 200 and r.text == "console.log(1)"


def test_루트_직하_정적_파일(tmp_path):
    client = TestClient(_app(_make_dist(tmp_path)))
    r = client.get("/favicon.svg")
    assert r.status_code == 200 and r.text == "<svg/>"


def test_api_미존재는_json_404를_유지한다(tmp_path):
    client = TestClient(_app(_make_dist(tmp_path)))
    r = client.get("/api/nothing-here")
    assert r.status_code == 404
    assert "SPA" not in r.text
    assert r.headers["content-type"].startswith("application/json")


def test_등록된_api는_그대로_동작한다(tmp_path):
    client = TestClient(_app(_make_dist(tmp_path)))
    assert client.get("/api/ping").json() == {"ok": True}


def test_ws와_docs_접두사도_폴백하지_않는다(tmp_path):
    client = TestClient(_app(_make_dist(tmp_path)))
    for path in ("/ws/nothing", "/openapi.jsonx"):
        # /ws/* 는 GET 폴백 대상이 아니다. openapi.jsonx 는 접두사 오탐 점검(정확히 일치만 제외).
        r = client.get(path)
        if path.startswith("/ws"):
            assert r.status_code == 404 and "SPA" not in r.text
        else:
            assert r.status_code == 200 and "SPA" in r.text


def test_경로_탈출은_막는다(tmp_path):
    dist = _make_dist(tmp_path)
    (tmp_path / "secret.txt").write_text("TOP-SECRET", encoding="utf-8")
    client = TestClient(_app(dist))
    for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/..%2fsecret.txt"):
        r = client.get(path)
        assert "TOP-SECRET" not in r.text, path


def test_health는_dist와_무관하게_응답한다():
    from app.main import app
    client = TestClient(app)
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"] is True
