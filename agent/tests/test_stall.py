"""Stall detector: fires only on "commanded to move but not moving"."""

from missiondebug_agent.detectors.stall import StallAnomaly, StallDetector

# (cmd linear/angular), (actual linear/angular)
STUCK = ((0.5, 0.0), (0.0, 0.0))    # told to move, odometry says not moving
IDLE = ((0.0, 0.0), (0.0, 0.0))     # not told to move, not moving (parked)
DRIVING = ((0.5, 0.0), (0.5, 0.0))  # told to move and moving


def _det(fired, **kw):
    kw.setdefault("stale_seconds", 2.0)
    return StallDetector(0.01, 5.0, 30.0, fired.append, **kw)


def _tick(det, ts, cmd, motion):
    det.update_cmd(cmd[0], cmd[1], ts)
    det.update_motion(motion[0], motion[1], ts)


def test_idle_robot_never_fires():
    # The bug this fixes: a parked robot (no move command) must not trigger.
    fired: list[StallAnomaly] = []
    det = _det(fired)
    for s in range(0, 11):
        _tick(det, int(s * 1e9), *IDLE)
    assert not fired


def test_driving_never_fires():
    fired: list[StallAnomaly] = []
    det = _det(fired)
    for s in range(0, 11):
        _tick(det, int(s * 1e9), *DRIVING)
    assert not fired


def test_stuck_fires_after_duration():
    fired: list[StallAnomaly] = []
    det = _det(fired)
    for s in range(0, 5):           # 0..4s commanded-but-not-moving
        _tick(det, int(s * 1e9), *STUCK)
    assert not fired                # < 5s
    _tick(det, int(5e9), *STUCK)    # 5s elapsed
    assert len(fired) == 1
    assert fired[0].started_at_ns == 0


def test_stale_odometry_does_not_fire():
    # Commanded to move but odometry is stale/absent: cannot confirm "not
    # moving", so we do not fire (that is a dropout, handled elsewhere).
    fired: list[StallAnomaly] = []
    det = _det(fired, stale_seconds=1.0)
    det.update_motion(0.0, 0.0, 0)        # one stale reading at t=0
    det.update_cmd(0.5, 0.0, 0)
    det.update_cmd(0.5, 0.0, int(6e9))    # 6s later, odom is now stale (>1s)
    assert not fired

    # And with no odometry at all, it also never fires.
    fired2: list[StallAnomaly] = []
    det2 = _det(fired2, stale_seconds=1.0)
    det2.update_cmd(0.5, 0.0, 0)
    det2.update_cmd(0.5, 0.0, int(6e9))
    assert not fired2


def test_actual_motion_resets_clock():
    fired: list[StallAnomaly] = []
    det = _det(fired)
    for s in range(0, 4):              # stuck 0..3s
        _tick(det, int(s * 1e9), *STUCK)
    _tick(det, int(4e9), *DRIVING)     # actually moving -> resets the stall clock
    for s in range(5, 9):              # stuck again 5..8s (only 4s)
        _tick(det, int(s * 1e9), *STUCK)
    assert not fired


def test_cooldown_blocks_refire():
    fired: list[StallAnomaly] = []
    det = _det(fired)
    for s in range(0, 41):             # continuously stuck 0..40s
        _tick(det, int(s * 1e9), *STUCK)
    # Fires at 5s, then blocked by the 30s cooldown until a fresh duration
    # accumulates -> second fire at 35s. Exactly two within 40s.
    assert len(fired) == 2


def test_angular_command_counts_as_commanded_to_move():
    fired: list[StallAnomaly] = []
    det = _det(fired)
    # Commanded to rotate but not moving at all -> still a stall.
    for s in range(0, 6):
        det.update_cmd(0.0, 0.5, int(s * 1e9))
        det.update_motion(0.0, 0.0, int(s * 1e9))
    assert len(fired) == 1
