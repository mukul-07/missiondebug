from missiondebug_agent.detectors.path_deviation import (
    PathDeviationAnomaly,
    PathDeviationDetector,
    min_distance_to_path,
)


# A simple straight path along x: (0,0) -> (10,0)
STRAIGHT_PATH = [(0.0, 0.0), (10.0, 0.0)]


def test_distance_helpers():
    assert min_distance_to_path(5.0, 0.0, STRAIGHT_PATH) == 0.0
    assert min_distance_to_path(5.0, 1.0, STRAIGHT_PATH) == 1.0
    # Off the end of the segment — clamps to endpoint, distance = sqrt(2^2+1^2)=2.236
    d = min_distance_to_path(12.0, 1.0, STRAIGHT_PATH)
    assert abs(d - (2**2 + 1**2) ** 0.5) < 1e-9


def test_no_fire_when_on_path():
    fired: list[PathDeviationAnomaly] = []
    det = PathDeviationDetector(0.5, 2.0, 30.0, fired.append)
    det.update_plan(STRAIGHT_PATH)
    for i in range(10):
        det.update_pose(float(i), 0.0, i * int(1e9))
    assert not fired


def test_drift_and_recover_no_fire():
    fired: list[PathDeviationAnomaly] = []
    det = PathDeviationDetector(0.5, 2.0, 30.0, fired.append)
    det.update_plan(STRAIGHT_PATH)
    det.update_pose(1.0, 1.0, 0)             # drift starts
    det.update_pose(2.0, 1.0, int(1.0e9))    # 1s of drift
    det.update_pose(3.0, 0.0, int(1.5e9))    # back on path -> reset
    det.update_pose(4.0, 0.0, int(3.0e9))
    assert not fired


def test_drift_and_fire():
    fired: list[PathDeviationAnomaly] = []
    det = PathDeviationDetector(0.5, 2.0, 30.0, fired.append)
    det.update_plan(STRAIGHT_PATH)
    det.update_pose(1.0, 1.0, 0)             # drift starts (1m off)
    det.update_pose(1.0, 1.0, int(1.5e9))    # not yet 2s
    assert not fired
    det.update_pose(1.0, 1.0, int(2.0e9))    # 2s elapsed -> fire
    assert len(fired) == 1
    assert fired[0].distance_m >= 0.5
    assert fired[0].started_at_ns == 0


def test_plan_change_resets_clock():
    fired: list[PathDeviationAnomaly] = []
    det = PathDeviationDetector(0.5, 2.0, 30.0, fired.append)
    det.update_plan(STRAIGHT_PATH)
    det.update_pose(1.0, 1.0, 0)             # drifting
    det.update_pose(1.0, 1.0, int(1.5e9))    # 1.5s drift
    # New plan that puts the robot on-path; clock resets.
    det.update_plan([(0.0, 1.0), (10.0, 1.0)])
    det.update_pose(1.0, 1.0, int(2.0e9))    # on the new path
    det.update_pose(1.0, 1.0, int(3.0e9))
    assert not fired


def test_cooldown_blocks_refire():
    fired: list[PathDeviationAnomaly] = []
    det = PathDeviationDetector(0.5, 2.0, 30.0, fired.append)
    det.update_plan(STRAIGHT_PATH)
    # First fire.
    det.update_pose(1.0, 1.0, 0)
    det.update_pose(1.0, 1.0, int(2.0e9))
    assert len(fired) == 1

    # Stays drifted within cooldown -> no refire.
    det.update_pose(1.0, 1.0, int(10.0e9))
    det.update_pose(1.0, 1.0, int(15.0e9))
    assert len(fired) == 1

    # After cooldown elapses, a fresh sustained drift fires again.
    det.update_pose(1.0, 1.0, int(35.0e9))   # starts new drift clock
    det.update_pose(1.0, 1.0, int(40.0e9))   # drift is 5s, cooldown elapsed
    assert len(fired) == 2


def test_no_fire_without_plan():
    fired: list[PathDeviationAnomaly] = []
    det = PathDeviationDetector(0.5, 2.0, 30.0, fired.append)
    det.update_pose(100.0, 100.0, 0)
    det.update_pose(100.0, 100.0, int(5.0e9))
    assert not fired


def test_invalid_threshold():
    import pytest
    with pytest.raises(ValueError):
        PathDeviationDetector(0, 2.0, 30.0, lambda _: None)
