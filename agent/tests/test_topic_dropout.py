from missiondebug_agent.config import TopicDropoutConfig
from missiondebug_agent.detectors.topic_dropout import (
    DropoutAnomaly,
    TopicDropoutDetector,
)


def _ns(s: float) -> int:
    return int(s * 1e9)


def test_does_not_fire_before_first_message():
    """Without ever having seen the topic, no anomaly. Avoids spurious
    fires at agent startup."""
    fired: list[DropoutAnomaly] = []
    det = TopicDropoutDetector(
        [TopicDropoutConfig(topic="/lidar", silence_seconds=2.0)],
        fired.append,
    )
    det.tick(_ns(1000))  # never saw a message
    assert not fired


def test_fires_after_silence_period():
    fired: list[DropoutAnomaly] = []
    det = TopicDropoutDetector(
        [TopicDropoutConfig(topic="/lidar", silence_seconds=2.0)],
        fired.append,
    )
    det.saw("/lidar", _ns(0))
    det.tick(_ns(1.5))   # only 1.5s of silence — not yet
    assert not fired
    det.tick(_ns(2.5))   # 2.5s of silence — fires
    assert len(fired) == 1
    assert fired[0].topic == "/lidar"
    assert fired[0].name == "dropout:/lidar"


def test_silence_resets_when_message_arrives():
    fired: list[DropoutAnomaly] = []
    det = TopicDropoutDetector(
        [TopicDropoutConfig(topic="/lidar", silence_seconds=2.0)],
        fired.append,
    )
    det.saw("/lidar", _ns(0))
    det.tick(_ns(1.5))
    det.saw("/lidar", _ns(1.6))   # message arrives
    det.tick(_ns(3.0))             # 1.4s since last — no fire
    assert not fired


def test_cooldown_blocks_refire():
    fired: list[DropoutAnomaly] = []
    det = TopicDropoutDetector(
        [TopicDropoutConfig(topic="/lidar", silence_seconds=2.0,
                             cooldown_seconds=10.0)],
        fired.append,
    )
    det.saw("/lidar", _ns(0))
    det.tick(_ns(2.5))
    assert len(fired) == 1

    # Still silent inside cooldown — no second fire.
    det.tick(_ns(5.0))
    det.tick(_ns(8.0))
    assert len(fired) == 1

    # After cooldown, fires again on continuing silence.
    det.tick(_ns(13.0))
    assert len(fired) == 2


def test_custom_name_is_honored():
    fired: list[DropoutAnomaly] = []
    det = TopicDropoutDetector(
        [TopicDropoutConfig(topic="/lidar", silence_seconds=1.0,
                             name="front-lidar-died")],
        fired.append,
    )
    det.saw("/lidar", _ns(0))
    det.tick(_ns(2.0))
    assert fired[0].name == "front-lidar-died"


def test_topics_independent():
    fired: list[DropoutAnomaly] = []
    det = TopicDropoutDetector(
        [
            TopicDropoutConfig(topic="/a", silence_seconds=1.0),
            TopicDropoutConfig(topic="/b", silence_seconds=5.0),
        ],
        fired.append,
    )
    det.saw("/a", _ns(0))
    det.saw("/b", _ns(0))
    det.tick(_ns(2.0))
    # /a should fire (1s threshold), /b should not (5s threshold)
    assert len(fired) == 1
    assert fired[0].topic == "/a"


def test_topics_property():
    det = TopicDropoutDetector(
        [
            TopicDropoutConfig(topic="/a", silence_seconds=1.0),
            TopicDropoutConfig(topic="/b", silence_seconds=1.0),
        ],
        lambda _a: None,
    )
    assert set(det.topics) == {"/a", "/b"}
