"""Cross-topic comparison engine: fires when two signals disagree."""

from types import SimpleNamespace

from missiondebug_agent.config import CompareOperand, CompareRuleConfig
from missiondebug_agent.detectors.compare import CompareAnomaly, CompareEngine


def _scalar(v):
    return SimpleNamespace(data=v)


def _engine(op, threshold, fired, **kw):
    kw.setdefault("duration_seconds", 5.0)
    kw.setdefault("stale_seconds", 2.0)
    rule = CompareRuleConfig(
        name="cmp",
        a=CompareOperand(topic="/a", field="data"),
        b=CompareOperand(topic="/b", field="data"),
        op=op, threshold=threshold, **kw,
    )
    return CompareEngine([rule], fired.append)


def _tick(eng, ts, a, b):
    eng.update("/a", _scalar(a), ts)
    eng.update("/b", _scalar(b), ts)


def test_a_active_b_idle_fires():
    fired: list[CompareAnomaly] = []
    eng = _engine("a_active_b_idle", 0.05, fired)
    for s in range(0, 5):
        _tick(eng, int(s * 1e9), 0.5, 0.0)   # commanded (a) but idle (b)
    assert not fired
    _tick(eng, int(5e9), 0.5, 0.0)
    assert len(fired) == 1


def test_a_active_b_idle_no_fire_when_b_moving():
    fired: list[CompareAnomaly] = []
    eng = _engine("a_active_b_idle", 0.05, fired)
    for s in range(0, 8):
        _tick(eng, int(s * 1e9), 0.5, 0.5)   # both active -> not idle
    assert not fired


def test_a_active_b_idle_no_fire_when_a_idle():
    fired: list[CompareAnomaly] = []
    eng = _engine("a_active_b_idle", 0.05, fired)
    for s in range(0, 8):
        _tick(eng, int(s * 1e9), 0.0, 0.0)   # a not commanded
    assert not fired


def test_diverge_gt_fires():
    fired: list[CompareAnomaly] = []
    eng = _engine("diverge_gt", 0.1, fired)
    for s in range(0, 6):
        _tick(eng, int(s * 1e9), 1.0, 0.5)   # |1.0-0.5| = 0.5 > 0.1
    assert len(fired) == 1


def test_diverge_gt_no_fire_when_close():
    fired: list[CompareAnomaly] = []
    eng = _engine("diverge_gt", 0.1, fired)
    for s in range(0, 8):
        _tick(eng, int(s * 1e9), 1.0, 0.95)  # diff 0.05 < 0.1
    assert not fired


def test_stale_operand_does_not_fire():
    fired: list[CompareAnomaly] = []
    eng = _engine("a_active_b_idle", 0.05, fired, stale_seconds=1.0)
    eng.update("/b", _scalar(0.0), 0)         # b at t=0
    eng.update("/a", _scalar(0.5), 0)
    eng.update("/a", _scalar(0.5), int(6e9))  # 6s later, b is stale (>1s)
    assert not fired


def test_cooldown_blocks_refire():
    fired: list[CompareAnomaly] = []
    eng = _engine("a_active_b_idle", 0.05, fired)  # duration 5, cooldown 30
    for s in range(0, 41):
        _tick(eng, int(s * 1e9), 0.5, 0.0)
    assert len(fired) == 2   # fires at 5s and 35s


def test_condition_resets_clock():
    fired: list[CompareAnomaly] = []
    eng = _engine("a_active_b_idle", 0.05, fired)
    for s in range(0, 4):
        _tick(eng, int(s * 1e9), 0.5, 0.0)   # diverging 0..3s
    _tick(eng, int(4e9), 0.5, 0.5)            # b matches -> condition false -> reset
    for s in range(5, 9):
        _tick(eng, int(s * 1e9), 0.5, 0.0)   # diverging again 5..8s (4s)
    assert not fired
