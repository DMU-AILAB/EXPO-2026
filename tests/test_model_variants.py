"""모델 선택 목록(`camera_config.MODEL_VARIANTS`)의 단일 출처 계약.

같은 목록이 여러 곳에 따로 있다 — Pi 설정, 서버의 `ModelVariant` enum, 대시보드 UI, `Makefile`의 배포 목록.
한쪽만 고치면 **조용히** 어긋난다(UI에 없는 모델이 선택되지 않거나, 서버가 기기의 모델을 422로 거부하거나,
`make sync`가 쓰지 않는 가중치를 계속 올린다). 그래서 여기서 전부 맞대어 본다.

선택지에서 뺀 모델(`RETIRED_MODEL_VARIANTS`)은 기존 기기의 설정에 이름이 남아 있을 수 있어서, 읽을 때 기본 모델로
대체한다 — 검증 실패로 설정이 통째로 무시되거나 기동이 죽으면 안 된다.
"""

import json
import re
from pathlib import Path

import pytest

import camera_config as cc

ROOT = Path(__file__).resolve().parent.parent
KEYS = {"v4_320", "v10_320", "v15_320"}


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_선택지는_세_개다():
    assert set(cc.MODEL_VARIANTS) == KEYS


def test_기본값은_v15이고_선택지_안에_있다():
    assert cc._DEFAULT_MODEL_VARIANT == "v15_320" and cc._DEFAULT_MODEL_VARIANT in cc.MODEL_VARIANTS


def test_서버_enum이_같다():
    src = _read("dashboard/backend/app/schemas/camera.py")
    body = src[src.index("class ModelVariant"):]
    body = body[:body.index("class CameraUpdate")]
    assert set(re.findall(r'=\s*"(v\w+)"', body)) == KEYS


def test_대시보드_UI_목록이_같다():
    src = _read("dashboard/frontend/src/pages/DeviceDetail.tsx")
    m = re.search(r"const MODEL_VARIANTS = \[([^\]]*)\]", src)
    assert m and set(re.findall(r"'(v\w+)'", m.group(1))) == KEYS


def test_배포_목록이_선택지의_가중치_폴더와_같다():
    mk = _read("Makefile")
    m = re.search(r"^DEPLOY_MODEL_DIRS\s*=\s*((?:[^\n\\]*\\\n)*[^\n]*)", mk, re.M)
    dirs = set(re.findall(r"runs/[\w.\-]+/weights", m.group(1).replace("\\\n", " ")))
    assert dirs == {v["weights_dir"] for v in cc.MODEL_VARIANTS.values()}


def test_신규_설치_기본_모델은_선택지_안에_있다():
    src = _read("dashboard/backend/app/services/bundle_builder.py")
    m = re.search(r"BOOTSTRAP_MODELS\s*=\s*\(([^)]*)\)", src)
    assert set(re.findall(r'"(v\w+)"', m.group(1))) <= KEYS


@pytest.mark.parametrize("key", sorted(KEYS))
def test_선택지의_CPU_가중치는_저장소에_있다(key):
    assert (ROOT / cc.MODEL_VARIANTS[key]["weights_dir"] / "best_int8.tflite").is_file()


def test_Coral_컴파일본은_v4에만_있다():
    """v10·v15의 Coral 컴파일본이 생기면 이 테스트를 갱신하고 문서의 한계 표기를 지울 것."""
    has = {k for k, v in cc.MODEL_VARIANTS.items()
           if (ROOT / v["weights_dir"] / "best_int8_edgetpu.tflite").is_file()}
    assert has == {"v4_320"}


RETIRED = {"v2_640", "v3_320", "v5b_320", "v6_320", "v11_yolo26n_320"}


def test_뺀_모델_목록():
    assert cc.RETIRED_MODEL_VARIANTS == RETIRED and not (RETIRED & set(cc.MODEL_VARIANTS))


@pytest.mark.parametrize("name", sorted(RETIRED))
def test_뺀_모델은_기본_모델로_대체한다(name, capsys):
    assert cc.normalize_model_variant(name) == "v15_320"
    assert "더 이상 선택할 수 없습니다" in capsys.readouterr().out


def test_정상_이름과_오타는_고치지_않는다():
    # 오타를 조용히 기본값으로 덮으면 원인을 찾을 수 없다 — 검증이 걸러야 한다
    assert cc.normalize_model_variant("v4_320") == "v4_320"
    assert cc.normalize_model_variant("v99_320") == "v99_320"
    assert cc.normalize_model_variant(None) is None


def _cfg(tmp_path, variant):
    p = tmp_path / "camera_config.json"
    p.write_text(json.dumps({"cameras": [{"id": "cam0", "port": 8080, "model_variant": variant}]}), encoding="utf-8")
    return p


@pytest.mark.parametrize("name", sorted(RETIRED))
def test_기존_설정의_뺀_모델은_읽을_때_대체되고_검증을_통과한다(tmp_path, name):
    profiles = cc.load_camera_config(_cfg(tmp_path, name))
    assert profiles[0].model_variant == "v15_320"
    assert cc.validate_camera_config(profiles) == []          # 설정이 통째로 무시되지 않는다


def test_알_수_없는_모델은_검증이_거부한다(tmp_path):
    profiles = cc.load_camera_config(_cfg(tmp_path, "v99_320"))
    assert any("model_variant" in e for e in cc.validate_camera_config(profiles))


def test_필드가_없으면_기본_모델이다(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"cameras": [{"id": "cam0", "port": 8080}]}), encoding="utf-8")
    assert cc.load_camera_config(p)[0].model_variant == "v15_320"


def test_CLI_모델_인자는_뺀_이름을_대체한다():
    """옛 systemd 유닛이 `--model-variant v2_640`을 넘겨도 기동이 죽지 않는다(argparse는 type 변환 뒤 choices를 본다)."""
    src = _read("device/camera_live_pi.py")
    assert "type=normalize_model_variant" in src and "default=_DEFAULT_MODEL_VARIANT" in src
    assert 'MODEL_VARIANTS["v2_640"]' not in src


def test_뺀_모델_이름이_코드에_남아_있지_않다():
    """선택지에서 뺀 이름을 직접 참조하는 곳이 있으면 KeyError나 검증 실패로 이어진다(예: `MODEL_VARIANTS["v2_640"]`)."""
    pattern = re.compile("|".join(sorted(RETIRED)))
    allowed = {"device/camera_config.py"}                      # RETIRED 집합 자체
    roots = ["device", "apps/roi_editor", "dashboard/backend/app", "dashboard/frontend/src", "deploy"]
    offenders = []
    for root in roots:
        for f in (ROOT / root).rglob("*"):
            if not f.is_file() or f.suffix not in {".py", ".ts", ".tsx", ".html", ".service", ".sh", ".js"}:
                continue
            rel = f.relative_to(ROOT).as_posix()
            if rel in allowed or "node_modules" in rel or "/dist/" in rel:
                continue
            for i, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if pattern.search(line) and not line.lstrip().startswith(("#", "//", "*")):
                    offenders.append(f"{rel}:{i}: {line.strip()[:90]}")
    assert not offenders, "\n".join(offenders)
