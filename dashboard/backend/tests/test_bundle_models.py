"""번들의 모델 범위(`none|default|all`).

신규 설치가 모델 파일 없이 끝나면 **탐지가 안 되는 기기**가 되고, 푸시 업데이트에서
`bundle_id`가 모델 때문에 달라지면 기기는 영원히 "구버전"으로 보인다. 둘 다 조용히
틀리는 종류라 테스트로 고정한다.
"""

import io
import tarfile

import pytest

from app.routers import bootstrap
from app.services import bundle_builder as bb
from app.services.bundle_builder import BOOTSTRAP_MODELS, BundleError, build_bundle, normalize_models


def _names(bundle) -> list[str]:
    with tarfile.open(fileobj=io.BytesIO(bundle.data), mode="r:gz") as tf:
        return tf.getnames()


def _models(bundle) -> list[str]:
    return [n for n in _names(bundle) if n.endswith(".tflite")]


def test_none은_모델을_싣지_않는다():
    assert _models(build_bundle("none")) == []
    assert _models(build_bundle()) == []                    # 기본값 — 푸시 업데이트가 쓴다


def test_default는_기본_모델만_싣는다():
    models = _models(build_bundle("default"))
    dirs = bb._model_variant_dirs(bb.repo_root())
    expected = {f"{dirs[k]}/best_int8.tflite" for k in BOOTSTRAP_MODELS}
    assert expected <= set(models)
    # edgetpu 컴파일본이 있어도 기본 변형 디렉터리 안의 것만이다
    allowed_dirs = {dirs[k] for k in BOOTSTRAP_MODELS}
    assert {m.rsplit("/", 1)[0] for m in models} <= allowed_dirs


def test_default는_all보다_작다():
    default, everything = build_bundle("default"), build_bundle("all")
    assert len(_models(default)) < len(_models(everything))
    assert len(default.data) < len(everything.data)


def test_예전_키워드_호출도_동작한다():
    assert set(_models(build_bundle(include_models=True))) == set(_models(build_bundle("all")))
    assert _models(build_bundle(include_models=False)) == []


def test_bool은_기존_의미를_유지한다():
    assert _models(build_bundle(False)) == []
    assert set(_models(build_bundle(True))) == set(_models(build_bundle("all")))


def test_bundle_id는_모델_범위와_무관하다():
    ids = {build_bundle(m).bundle_id for m in ("none", "default", "all")}
    assert len(ids) == 1


def test_같은_입력이면_바이트도_같다():
    assert build_bundle("default").data == build_bundle("default").data


def test_알_수_없는_모드는_거부한다():
    with pytest.raises(BundleError):
        normalize_models("everything")


def test_기본_모델_순서는_채택_후보가_앞이다():
    assert BOOTSTRAP_MODELS == ("v15_320", "v10_320")


def test_기본_모델_키는_MODEL_VARIANTS에_실재한다():
    variants = bb._model_variant_dirs(bb.repo_root())
    for key in BOOTSTRAP_MODELS:
        assert key in variants, f"{key}가 camera_config.MODEL_VARIANTS에 없다"


def test_모델_경로를_camera_config에서_읽는다():
    # 값 복제 금지 — 실제 모듈의 표와 일치해야 한다
    import camera_config
    assert bb._model_variant_dirs(bb.repo_root()) == {
        k: v["weights_dir"] for k, v in camera_config.MODEL_VARIANTS.items()}


def test_가중치가_하나도_없으면_실패한다(tmp_path, monkeypatch):
    # 모델 없는 번들은 탐지가 안 되는 기기를 조용히 만든다
    import shutil
    root = bb.repo_root()
    fake = tmp_path / "repo"
    (fake / "device").mkdir(parents=True)
    shutil.copy(root / "device" / "camera_config.py", fake / "device" / "camera_config.py")
    shutil.copy(root / "Makefile", fake / "Makefile")
    monkeypatch.setattr(bb, "repo_root", lambda: fake)
    with pytest.raises(BundleError):
        bb._collect(fake, "default")


def test_self_update는_default_번들의_모델을_받아들인다():
    """설치 스크립트가 `--include-models`로 적용할 때 허용 목록에 걸리지 않아야 한다."""
    import self_update
    for key in BOOTSTRAP_MODELS:
        rel = bb._model_variant_dirs(bb.repo_root())[key] + "/best_int8.tflite"
        assert self_update.is_allowed(rel, include_models=True)
        assert not self_update.is_allowed(rel, include_models=False)


# --- 라우터 -------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _tokens():
    bootstrap._tokens.clear()
    yield
    bootstrap._tokens.clear()


def _get(client, query):
    token, _ = bootstrap.issue_token()
    return client.get(f"/api/bootstrap/bundle.tar.gz?token={token}{query}")


def test_라우터_models_쿼리(client):
    none = _get(client, "")
    default = _get(client, "&models=default")
    alias = _get(client, "&include_models=true")
    everything = _get(client, "&models=all")
    assert none.status_code == default.status_code == alias.status_code == 200
    assert len(none.content) < len(default.content) < len(everything.content)
    assert alias.content == everything.content              # 예전 쿼리는 all의 별칭
    assert none.headers["X-Bundle-Id"] == default.headers["X-Bundle-Id"]


def test_라우터는_잘못된_models를_400으로_거부한다(client):
    r = _get(client, "&models=bogus")
    assert r.status_code == 400
    assert r.json()["error"] == "INVALID_MODELS"              # errors.py가 detail을 최상위 봉투로 편다


def test_models가_include_models보다_우선한다(client):
    r = _get(client, "&models=none&include_models=true")
    assert r.status_code == 200
    none = _get(client, "&models=none")
    assert r.content == none.content
