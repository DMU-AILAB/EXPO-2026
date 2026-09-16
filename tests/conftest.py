"""pytest 공용 경로 설정.

재배치 전에는 모든 테스트가 `sys.path.insert(0, parent.parent)` 한 줄로 루트의
모듈을 import했다. 지금은 소스가 기능별로 나뉘어 있어 넣어야 할 경로가 셋이다.
20개 파일에 같은 코드를 반복하는 대신 여기 한 곳에 모은다 — pytest가 테스트
수집 전에 conftest.py를 먼저 불러오므로 개별 파일의 삽입보다 항상 앞선다.

`device/`가 별도 경로인 이유는 배포 때문이다. rsync가 device/*.py를 Pi의
~/visionguide/ 에 **평면으로** 풀어놓아서, 기기에서는 이 디렉터리가 존재하지
않는다. 테스트는 PC 트리에서만 돌므로 여기서는 항상 device/를 넣으면 된다.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# tools/ 는 기능별 하위 디렉터리(data·eval·dev)로 나뉘어 있고 각 스크립트는
# 패키지가 아니라 단독 실행 스크립트라, 상위 디렉터리만 넣으면 import되지 않는다.
_PATHS = [ROOT, ROOT / "device", ROOT / "apps"]
if (ROOT / "tools").is_dir():
    _PATHS += sorted(p for p in (ROOT / "tools").iterdir() if p.is_dir())

for _p in _PATHS:
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
