"""v2 P1.4: save_now forwards session metadata to hub_client when provided.

Hard Rule 18: when hub_client is None (v1.5 standalone mode), nothing
about save_now's behavior changes — same MCAP, same response, same
side effects.
"""

from __future__ import annotations

from pathlib import Path

from missiondebug_agent.config import AgentConfig, TopicConfig
from missiondebug_agent.http_api import save_now
from missiondebug_agent.hub_client import HubClient, HubClientConfig, fake_post_factory
from missiondebug_agent.ring_buffer import BufferedMessage, RingBuffer


def _loader(_t: str) -> str:
    return "string data\n"


def _config(tmp_path: Path, robot_id: str = "robot-001") -> AgentConfig:
    return AgentConfig(
        robot_id=robot_id,
        buffer_seconds=60.0,
        topics=[TopicConfig(name="/cmd_vel", type="geometry_msgs/msg/Twist")],
        output_dir=str(tmp_path),
    )


def _ring(n: int = 5) -> RingBuffer:
    r = RingBuffer(window_seconds=60.0)
    for i in range(n):
        r.append(BufferedMessage(
            timestamp_ns=i * 100_000_000,
            wall_ns=1_700_000_000_000_000_000 + i * 100_000_000,
            topic="/cmd_vel",
            payload=b"\x00" * 4,
        ))
    return r


def test_save_now_without_hub_client_works_unchanged(tmp_path):
    """v1.5 path: no hub configured, no hub calls happen."""
    resp = save_now(
        _config(tmp_path), _ring(),
        label="test",
        schema_loader=_loader,
    )
    assert resp.session_id.startswith("robot-001_")
    assert Path(resp.path).exists()


def test_save_now_with_hub_client_forwards_metadata(tmp_path):
    """When hub_client is provided, the session is also pushed to the hub."""
    recorder, post_fn = fake_post_factory()
    client = HubClient(
        HubClientConfig(
            hub_url="http://hub:8000",
            robot_id="robot-001",
            agent_url="http://robot-001.local:7000",
            agent_version="1.5.0",
        ),
        post_fn=post_fn,
    )
    resp = save_now(
        _config(tmp_path), _ring(),
        label="anomaly:test",
        schema_loader=_loader,
        hub_client=client,
    )
    # The MCAP write still happened.
    assert Path(resp.path).exists()
    # Exactly one ingest POST.
    ingests = [r for r in recorder if r[0].endswith("/api/v1/sessions/ingest")]
    assert len(ingests) == 1
    _url, body, _auth = ingests[0]
    assert body["session_id"] == resp.session_id
    assert body["robot_id"] == "robot-001"
    assert body["label"] == "anomaly:test"
    assert body["mcap_size_bytes"] > 0
    assert body["topics"] == ["/cmd_vel"]
    # mcap_url auto-derived from agent_url + session_id.
    assert body["mcap_url"].startswith("http://robot-001.local:7000/api/sessions/")
    # Timestamps in ms.
    assert body["started_at"] > 0
    assert body["ended_at"] >= body["started_at"]
    assert body["duration_ms"] >= 0


def test_save_now_hub_failure_does_not_break_local_save(tmp_path):
    """Hard Rule 18: a broken hub must not prevent the local session save."""
    def broken(url, body, **kwargs):
        raise RuntimeError("simulated hub outage")
    client = HubClient(
        HubClientConfig(
            hub_url="http://hub:8000",
            robot_id="robot-001",
            agent_url="http://robot-001.local:7000",
        ),
        post_fn=broken,
    )
    # Should still return a SaveResponse without raising.
    resp = save_now(
        _config(tmp_path), _ring(),
        label="x",
        schema_loader=_loader,
        hub_client=client,
    )
    assert Path(resp.path).exists()
    assert client.sessions_failed == 1
    assert client.sessions_sent == 0
