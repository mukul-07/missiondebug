from missiondebug_agent.anomaly import StallAnomaly, StallDetector


def test_stall_fires_after_duration():
    fired: list[StallAnomaly] = []
    det = StallDetector(0.01, 5.0, 30.0, fired.append)

    det.update(0.0, 0.0, 0)
    det.update(0.0, 0.0, int(4e9))  # not yet
    assert not fired
    det.update(0.0, 0.0, int(5e9))  # now elapsed >= 5s
    assert len(fired) == 1
    assert fired[0].started_at_ns == 0


def test_motion_resets_stall_clock():
    fired: list[StallAnomaly] = []
    det = StallDetector(0.01, 5.0, 30.0, fired.append)

    det.update(0.0, 0.0, 0)
    det.update(1.0, 0.0, int(4e9))  # motion!
    det.update(0.0, 0.0, int(5e9))  # restart stall clock here
    det.update(0.0, 0.0, int(9e9))  # only 4s of stall
    assert not fired


def test_cooldown_blocks_refire():
    fired: list[StallAnomaly] = []
    det = StallDetector(0.01, 5.0, 30.0, fired.append)
    # Fire once.
    det.update(0.0, 0.0, 0)
    det.update(0.0, 0.0, int(6e9))
    assert len(fired) == 1

    # Continue to be stalled — must not re-fire within 30s of last fire.
    det.update(0.0, 0.0, int(20e9))
    det.update(0.0, 0.0, int(26e9))
    assert len(fired) == 1

    # After cooldown elapses and another full stall accumulates, fires again.
    det.update(0.0, 0.0, int(40e9))   # starts new stall clock
    det.update(0.0, 0.0, int(50e9))   # 10s later, well past 5s, and 50s > 30s cooldown
    assert len(fired) == 2


def test_angular_motion_counts_as_moving():
    fired: list[StallAnomaly] = []
    det = StallDetector(0.01, 5.0, 30.0, fired.append)
    det.update(0.0, 0.5, 0)
    det.update(0.0, 0.5, int(6e9))
    assert not fired
