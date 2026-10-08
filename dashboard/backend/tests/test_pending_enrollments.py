"""승인 대기 저장소 — 15분 무요청 만료, 거절 1시간 억제.

시계는 주입한다(진짜 15분을 기다릴 수 없다).
"""

from app.services.pending_enrollments import PENDING_TTL_SEC, REJECT_TTL_SEC, PendingStore


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _store():
    clock = Clock()
    return PendingStore(clock=clock, wall=lambda: 1_700_000_000.0 + clock.t), clock


def _add(store, ip="10.0.0.7", **kw):
    return store.upsert(ip, device_id=kw.get("device_id", "pi-x"), hostname=kw.get("hostname", "raspberrypi"),
                        current_server_url=kw.get("url", "http://192.168.0.32:8000"))


def test_기본값은_15분과_1시간이다():
    assert PENDING_TTL_SEC == 15 * 60 and REJECT_TTL_SEC == 60 * 60


def test_요청이_오면_목록에_올라온다():
    store, _ = _store()
    _add(store)
    [e] = store.list()
    assert e.ip == "10.0.0.7" and e.current_server_url == "http://192.168.0.32:8000"


def test_같은_기기가_다시_요청하면_갱신하고_중복되지_않는다():
    store, clock = _store()
    first = _add(store)
    clock.t += 60
    _add(store, url="http://192.168.0.99:8000", hostname="renamed")
    [e] = store.list()
    assert e.first_seen == first.first_seen                    # 처음 본 시각은 유지
    assert e.last_seen == clock.t                              # 마지막 요청은 갱신
    assert e.current_server_url == "http://192.168.0.99:8000" and e.hostname == "renamed"


def test_15분_동안_요청이_없으면_만료된다():
    store, clock = _store()
    _add(store)
    clock.t += PENDING_TTL_SEC - 1
    assert len(store.list()) == 1
    clock.t += 2
    assert store.list() == [] and store.get("10.0.0.7") is None


def test_요청이_계속_오면_만료되지_않는다():
    store, clock = _store()
    for _ in range(10):
        _add(store)
        clock.t += PENDING_TTL_SEC - 60                         # 매번 만료 직전에 다시 요청
    assert len(store.list()) == 1


def test_거절하면_지우고_1시간_억제한다():
    store, clock = _store()
    _add(store)
    assert store.reject("10.0.0.7") is True
    assert store.list() == [] and store.is_rejected("10.0.0.7")
    clock.t += REJECT_TTL_SEC - 1
    assert store.is_rejected("10.0.0.7")
    clock.t += 2
    assert not store.is_rejected("10.0.0.7")


def test_대기_중이_아닌_주소도_거절할_수_있다():
    store, _ = _store()
    assert store.reject("10.0.0.9") is False
    assert store.is_rejected("10.0.0.9")


def test_억제는_다른_기기에_영향이_없다():
    store, _ = _store()
    store.reject("10.0.0.7")
    assert not store.is_rejected("10.0.0.8")


def test_먼저_온_순서로_정렬한다():
    store, clock = _store()
    _add(store, "10.0.0.8")
    clock.t += 1
    _add(store, "10.0.0.7")
    assert [e.ip for e in store.list()] == ["10.0.0.8", "10.0.0.7"]


def test_외부로_내보내는_표현은_단조시계_값을_숨긴다():
    store, _ = _store()
    d = PendingStore.to_dict(_add(store))
    assert set(d) == {"ip", "device_id", "hostname", "current_server_url", "first_seen", "last_seen"}
    assert d["first_seen"] > 1_000_000_000                       # 벽시계(표시용)
