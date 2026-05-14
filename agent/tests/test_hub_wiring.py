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


# ---- v2 P5a: S3 upload integration -----------------------------------


def test_save_now_with_s3_uploader_posts_s3_url_to_hub(tmp_path):
    """When an S3Uploader is provided, the upload runs after the local
    write, and the hub payload carries the S3 URL as mcap_url instead
    of the agent-derived URL."""
    from missiondebug_agent.config import S3Config
    from missiondebug_agent.s3_uploader import S3Uploader

    class FakeS3Client:
        def __init__(self):
            self.calls = []
        def upload_file(self, Filename, Bucket, Key, ExtraArgs=None):
            self.calls.append({"filename": Filename, "bucket": Bucket, "key": Key})

    fake_s3 = FakeS3Client()
    uploader = S3Uploader(
        S3Config(
            bucket="my-bucket",
            region="us-east-1",
            public_base_url="https://my-bucket.s3.us-east-1.amazonaws.com",
            key_prefix="missiondebug/sessions",
        ),
        client=fake_s3,
    )

    recorder, post_fn = fake_post_factory()
    hub = HubClient(
        HubClientConfig(
            hub_url="http://hub:8000",
            robot_id="robot-001",
            agent_url="http://robot-001.local:7000",
        ),
        post_fn=post_fn,
    )

    resp = save_now(
        _config(tmp_path), _ring(),
        label="x",
        schema_loader=_loader,
        hub_client=hub,
        s3_uploader=uploader,
    )

    # Local MCAP still exists (Hard Rule 18 + 19 spirit — local first).
    assert Path(resp.path).exists()

    # S3 upload was attempted with the expected bucket/key.
    assert len(fake_s3.calls) == 1
    call = fake_s3.calls[0]
    assert call["bucket"] == "my-bucket"
    assert call["key"].startswith("missiondebug/sessions/robot-001/")
    assert call["key"].endswith(".mcap")

    # The hub got the S3 URL as mcap_url.
    ingests = [r for r in recorder if r[0].endswith("/api/v1/sessions/ingest")]
    assert len(ingests) == 1
    _url, body, _auth = ingests[0]
    assert body["mcap_url"].startswith("https://my-bucket.s3.us-east-1.amazonaws.com/")
    assert body["mcap_url"].endswith(".mcap")


def test_save_now_with_failed_s3_falls_back_to_agent_url(tmp_path):
    """S3 outage = non-fatal. The hub payload falls back to the
    agent-derived URL so the session is still reachable (just from the
    robot's local HTTP endpoint, which is the v2 Phase 1 behaviour)."""
    from missiondebug_agent.config import S3Config
    from missiondebug_agent.s3_uploader import S3Uploader

    class BrokenS3Client:
        def upload_file(self, **kwargs):
            raise RuntimeError("S3 unreachable")

    uploader = S3Uploader(
        S3Config(
            bucket="my-bucket",
            public_base_url="https://my-bucket.example.com",
        ),
        client=BrokenS3Client(),
    )

    recorder, post_fn = fake_post_factory()
    hub = HubClient(
        HubClientConfig(
            hub_url="http://hub:8000",
            robot_id="robot-001",
            agent_url="http://robot-001.local:7000",
        ),
        post_fn=post_fn,
    )

    resp = save_now(
        _config(tmp_path), _ring(),
        label="x",
        schema_loader=_loader,
        hub_client=hub,
        s3_uploader=uploader,
    )

    assert Path(resp.path).exists()
    # The hub still got an ingest — mcap_url falls through to the agent's
    # own /api/sessions/<id>/mcap (built by HubClient's _enrich_session_payload).
    ingests = [r for r in recorder if r[0].endswith("/api/v1/sessions/ingest")]
    assert len(ingests) == 1
    _url, body, _auth = ingests[0]
    # Not the S3 URL — fallback to agent-derived URL.
    assert "my-bucket.example.com" not in body["mcap_url"]
    assert "/api/sessions/" in body["mcap_url"]
    assert uploader.uploads_failed == 1
