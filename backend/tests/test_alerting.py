"""v2 P6 — webhook alerting (Slack / PagerDuty / generic)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from missiondebug_backend.alerting import (
    AlertConfig,
    Alerter,
    AlertEvent,
    WebhookAlerter,
    build_alerter,
    generic_payload,
    pagerduty_payload,
    slack_payload,
)
from missiondebug_backend.main import build_app

_EVENT = AlertEvent(
    session_id="SES-9",
    robot_id="bot-7",
    subsystem="power",
    rule="battery_low",
    summary="battery_low on bot-7 across /battery_state\nmore detail",
    prior_occurrences=2,  # => 3rd occurrence
)


class RecordingPost:
    def __init__(self, status: int = 200, fail_urls: tuple[str, ...] = ()):
        self.calls: list[tuple[str, dict]] = []
        self.status = status
        self.fail_urls = set(fail_urls)

    def __call__(self, url: str, body: dict) -> int:
        self.calls.append((url, body))
        if url in self.fail_urls:
            raise RuntimeError("connection refused")
        return self.status


# ---- payload builders --------------------------------------------------


def test_slack_payload_shape():
    p = slack_payload(_EVENT, public_url="http://hub.local")
    text = p["text"]
    assert "battery_low" in text
    assert "bot-7" in text
    assert "power" in text
    assert "3rd occurrence" in text
    assert "http://hub.local/sessions/SES-9" in text


def test_pagerduty_payload_shape():
    p = pagerduty_payload(_EVENT, routing_key="RK", public_url="http://hub.local")
    assert p["routing_key"] == "RK"
    assert p["event_action"] == "trigger"
    assert p["dedup_key"] == "missiondebug-battery_low-bot-7"
    assert p["payload"]["severity"] == "warning"
    assert p["payload"]["source"] == "bot-7"
    assert p["links"][0]["href"] == "http://hub.local/sessions/SES-9"


def test_generic_payload_shape():
    p = generic_payload(_EVENT, public_url="http://hub.local")
    assert p["event"] == "incident.captured"
    assert p["session_id"] == "SES-9"
    assert p["rule"] == "battery_low"
    assert p["occurrence"] == 3
    assert p["url"] == "http://hub.local/sessions/SES-9"


def test_payloads_handle_missing_public_url_and_summary():
    bare = AlertEvent(session_id="S", robot_id="r", subsystem=None, rule=None, summary=None)
    assert slack_payload(bare, "")["text"]  # no crash, no link
    assert "links" not in pagerduty_payload(bare, "RK", "")
    assert generic_payload(bare, "")["url"] == ""


# ---- dispatch ----------------------------------------------------------


def test_no_destinations_is_noop():
    alerter = build_alerter(AlertConfig())
    assert isinstance(alerter, Alerter)
    assert alerter.enabled is False
    assert alerter.alert_capture(_EVENT) == []


def test_dispatches_to_all_configured_destinations():
    cfg = AlertConfig(
        slack_webhook="http://slack",
        pagerduty_routing_key="RK",
        generic_webhook="http://generic",
        public_url="http://hub.local",
    )
    post = RecordingPost()
    alerter = WebhookAlerter(cfg, post_fn=post)

    deliveries = alerter.alert_capture(_EVENT)

    urls = {url for url, _ in post.calls}
    assert "http://slack" in urls
    assert "http://generic" in urls
    assert any("pagerduty" in u for u in urls)
    assert all(d.ok for d in deliveries)
    assert {d.destination for d in deliveries} == {"slack", "pagerduty", "webhook"}


def test_one_failure_does_not_block_others():
    cfg = AlertConfig(slack_webhook="http://slack", generic_webhook="http://generic")
    post = RecordingPost(fail_urls=("http://slack",))
    alerter = WebhookAlerter(cfg, post_fn=post)

    deliveries = alerter.alert_capture(_EVENT)

    by_dest = {d.destination: d for d in deliveries}
    assert by_dest["slack"].ok is False
    assert by_dest["webhook"].ok is True  # the generic one still went out
    # both were attempted
    assert len(post.calls) == 2


def test_non_2xx_marked_not_ok():
    cfg = AlertConfig(generic_webhook="http://generic")
    alerter = WebhookAlerter(cfg, post_fn=RecordingPost(status=500))
    deliveries = alerter.alert_capture(_EVENT)
    assert deliveries[0].ok is False
    assert deliveries[0].detail == "500"


def test_cooldown_throttles_repeat_for_same_rule_and_robot():
    cfg = AlertConfig(generic_webhook="http://generic", cooldown_s=300.0)
    post = RecordingPost()
    alerter = WebhookAlerter(cfg, post_fn=post)

    first = alerter.alert_capture(_EVENT)
    second = alerter.alert_capture(_EVENT)

    assert first[0].destination == "webhook"
    assert second == [first[0].__class__(destination="*", ok=True, detail="throttled")]
    assert len(post.calls) == 1  # second never hit the wire


def test_cooldown_disabled_allows_repeat():
    cfg = AlertConfig(generic_webhook="http://generic", cooldown_s=0.0)
    post = RecordingPost()
    alerter = WebhookAlerter(cfg, post_fn=post)
    alerter.alert_capture(_EVENT)
    alerter.alert_capture(_EVENT)
    assert len(post.calls) == 2


def test_cooldown_keyed_per_rule_and_robot():
    cfg = AlertConfig(generic_webhook="http://generic", cooldown_s=300.0)
    post = RecordingPost()
    alerter = WebhookAlerter(cfg, post_fn=post)
    alerter.alert_capture(_EVENT)
    other_robot = AlertEvent(
        session_id="SES-10", robot_id="bot-99", subsystem="power",
        rule="battery_low", summary="x",
    )
    alerter.alert_capture(other_robot)
    assert len(post.calls) == 2  # different robot → not throttled


# ---- wiring ------------------------------------------------------------


class RecordingAlerter(Alerter):
    """Records events synchronously so the ingest wiring is testable without
    the real daemon-thread dispatch."""

    enabled = True

    def __init__(self):
        self.events: list[AlertEvent] = []

    def alert_capture_in_background(self, event: AlertEvent) -> None:
        self.events.append(event)


def _ingest(client, sid="SES-1", label="anomaly:battery_low"):
    return client.post(
        "/api/v1/sessions/ingest",
        json={
            "session_id": sid,
            "robot_id": "bot-1",
            "started_at": 1_700_000_000_000,
            "ended_at": 1_700_000_060_000,
            "duration_ms": 60_000,
            "label": label,
            "topics": ["/battery_state"],
            "mcap_size_bytes": 100,
            "mcap_url": "http://agent.local/mcap",
            "subsystem": "power",
            "summary": "battery_low on bot-1",
        },
    )


def test_ingest_fires_alert(tmp_path):
    alerter = RecordingAlerter()
    app = build_app(tmp_path / "s", tmp_path / "db.sqlite3", alerter=alerter)
    with TestClient(app) as client:
        assert _ingest(client).status_code == 200
    assert len(alerter.events) == 1
    ev = alerter.events[0]
    assert ev.session_id == "SES-1"
    assert ev.robot_id == "bot-1"
    assert ev.rule == "battery_low"  # label prefix stripped


def test_admin_test_alert_endpoint(tmp_path):
    cfg = AlertConfig(generic_webhook="http://generic")
    post = RecordingPost()
    app = build_app(
        tmp_path / "s", tmp_path / "db.sqlite3", alerter=WebhookAlerter(cfg, post_fn=post)
    )
    with TestClient(app) as client:
        assert client.get("/api/admin/alerts").json()["enabled"] is True
        r = client.post("/api/admin/alerts/test")
        assert r.status_code == 200
        body = r.json()
        assert body["enabled"] is True
        assert body["deliveries"][0]["destination"] == "webhook"
        assert body["deliveries"][0]["ok"] is True
    assert len(post.calls) == 1
    assert post.calls[0][1]["session_id"] == "test-alert"
