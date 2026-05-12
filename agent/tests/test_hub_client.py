"""v2 P1.3: agent hub_client posts session metadata + heartbeats.

Pure logic tests — no network, no rclpy. The HTTP transport is
injectable so we can assert exactly what would be sent over the wire.
"""

from __future__ import annotations

import time

from missiondebug_agent.hub_client import (
    HubClient,
    HubClientConfig,
    fake_post_factory,
)


def _cfg(**overrides) -> HubClientConfig:
    base = dict(
        hub_url="http://hub.example:8000",
        robot_id="robot-001",
        agent_url="http://robot-001.local:7000",
        auth_token=None,
        agent_version="1.5.0",
        subsystem=None,
        heartbeat_interval_seconds=0.05,  # fast for tests
    )
    base.update(overrides)
    return HubClientConfig(**base)


# ---- session ingest ---------------------------------------------------


def test_report_session_posts_to_ingest_url():
    recorder, post_fn = fake_post_factory()
    client = HubClient(_cfg(), post_fn=post_fn)
    ok = client.report_session({
        "session_id": "abc",
        "started_at": 0, "ended_at": 1000, "duration_ms": 1000,
        "label": "anomaly:stall",
        "topics": ["/cmd_vel"],
        "mcap_size_bytes": 42,
    })
    assert ok is True
    assert client.sessions_sent == 1
    assert client.sessions_failed == 0
    assert len(recorder) == 1
    url, body, _auth = recorder[0]
    assert url == "http://hub.example:8000/api/v1/sessions/ingest"
    assert body["session_id"] == "abc"
    # Auto-enriched fields:
    assert body["robot_id"] == "robot-001"
    assert body["agent_url"] == "http://robot-001.local:7000"
    assert body["agent_version"] == "1.5.0"
    assert body["mcap_url"] == "http://robot-001.local:7000/api/sessions/abc/mcap"


def test_report_session_respects_explicit_mcap_url():
    """If the caller provides mcap_url (e.g. S3 upload), don't overwrite it."""
    recorder, post_fn = fake_post_factory()
    client = HubClient(_cfg(), post_fn=post_fn)
    client.report_session({
        "session_id": "abc",
        "started_at": 0, "ended_at": 1, "duration_ms": 1,
        "topics": [], "mcap_size_bytes": 0,
        "mcap_url": "s3://my-bucket/sessions/abc.mcap",
    })
    _url, body, _auth = recorder[0]
    assert body["mcap_url"] == "s3://my-bucket/sessions/abc.mcap"


def test_report_session_propagates_subsystem():
    recorder, post_fn = fake_post_factory()
    client = HubClient(_cfg(subsystem="navigation"), post_fn=post_fn)
    client.report_session({
        "session_id": "abc",
        "started_at": 0, "ended_at": 1, "duration_ms": 1,
        "topics": [], "mcap_size_bytes": 0,
    })
    _url, body, _auth = recorder[0]
    assert body["subsystem"] == "navigation"


def test_report_session_failure_is_non_fatal():
    """Hard Rule 18: a hub outage must not break the agent's local save."""
    def broken_post(url, body, **kwargs):
        raise RuntimeError("simulated hub outage")
    client = HubClient(_cfg(), post_fn=broken_post)
    ok = client.report_session({
        "session_id": "x", "started_at": 0, "ended_at": 1, "duration_ms": 1,
        "topics": [], "mcap_size_bytes": 0,
    })
    assert ok is False
    assert client.sessions_sent == 0
    assert client.sessions_failed == 1


def test_auth_token_passed_through():
    recorder, post_fn = fake_post_factory()
    client = HubClient(_cfg(auth_token="secret123"), post_fn=post_fn)
    client.report_session({
        "session_id": "x", "started_at": 0, "ended_at": 1, "duration_ms": 1,
        "topics": [], "mcap_size_bytes": 0,
    })
    _url, _body, auth = recorder[0]
    assert auth == "secret123"


# ---- heartbeat thread -------------------------------------------------


def test_heartbeat_thread_pings_repeatedly():
    """Start the thread, let it tick a few times, then stop."""
    recorder, post_fn = fake_post_factory()
    client = HubClient(_cfg(heartbeat_interval_seconds=0.03), post_fn=post_fn)
    client.start()
    try:
        # Wait long enough for ~3 ticks at 30ms.
        time.sleep(0.15)
    finally:
        client.stop()
    # Heartbeat thread waits for one interval BEFORE its first ping, so
    # in 150ms with 30ms interval we should see at least 2 pings.
    hb_calls = [r for r in recorder if r[0].endswith("/api/v1/agents/heartbeat")]
    assert len(hb_calls) >= 2
    # Payload shape on the wire:
    _url, body, _auth = hb_calls[0]
    assert body["robot_id"] == "robot-001"
    assert body["agent_url"] == "http://robot-001.local:7000"


def test_heartbeat_failure_backs_off_but_keeps_trying():
    """A flaky hub causes backoff but the loop doesn't die."""
    calls = {"n": 0}

    def flaky(url, body, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("simulated transient failure")
        # Then it recovers — but we won't wait long enough to see it.

    client = HubClient(_cfg(heartbeat_interval_seconds=0.02), post_fn=flaky)
    client.start()
    try:
        time.sleep(0.2)
    finally:
        client.stop()
    # Should have at least attempted multiple times and recorded the
    # initial failures.
    assert client.heartbeats_failed >= 1
    # And the loop kept running — proves backoff didn't kill the thread.


def test_start_is_idempotent():
    """Calling start() twice does not spawn two threads."""
    _r, post_fn = fake_post_factory()
    client = HubClient(_cfg(heartbeat_interval_seconds=1.0), post_fn=post_fn)
    client.start()
    t1 = client._thread
    client.start()
    t2 = client._thread
    assert t1 is t2
    client.stop()


def test_stop_without_start_is_safe():
    """stop() on a never-started client is a no-op."""
    _r, post_fn = fake_post_factory()
    client = HubClient(_cfg(), post_fn=post_fn)
    client.stop()  # should not raise
