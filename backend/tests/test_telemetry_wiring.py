"""v2 OTel — the hub calls into telemetry at the right moments.

Uses a recording Telemetry double injected via build_app(telemetry=...),
so this verifies the wiring without needing the OpenTelemetry SDK.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from missiondebug_backend.main import build_app
from missiondebug_backend.telemetry import Telemetry


class RecordingTelemetry(Telemetry):
    enabled = True

    def __init__(self):
        self.captures: list[tuple] = []
        self.resolutions: list[str] = []
        self.incidents: list[tuple] = []
        self.shutdowns = 0

    def record_capture(self, *, robot_id, subsystem, rule):
        self.captures.append((robot_id, subsystem, rule))

    def record_resolution(self, *, status):
        self.resolutions.append(status)

    def emit_incident(
        self, *, session_id, robot_id, subsystem, rule, summary, prior_occurrences=None
    ):
        self.incidents.append((session_id, robot_id, rule, prior_occurrences))

    def shutdown(self):
        self.shutdowns += 1


def _ingest(client, sid, robot="bot-1", label="anomaly:battery_low"):
    return client.post(
        "/api/v1/sessions/ingest",
        json={
            "session_id": sid,
            "robot_id": robot,
            "started_at": 1_700_000_000_000,
            "ended_at": 1_700_000_060_000,
            "duration_ms": 60_000,
            "label": label,
            "topics": ["/battery_state"],
            "mcap_size_bytes": 100,
            "mcap_url": "http://agent.local/mcap",
            "subsystem": "power",
            "summary": "a summary",
        },
    )


def _app(tmp_path: Path, rec):
    return build_app(tmp_path / "sessions", tmp_path / "db.sqlite3", telemetry=rec)


def test_ingest_records_capture_and_event(tmp_path: Path):
    rec = RecordingTelemetry()
    with TestClient(_app(tmp_path, rec)) as client:
        assert _ingest(client, "SES-1").status_code == 200
        assert rec.captures == [("bot-1", "power", "battery_low")]
        assert len(rec.incidents) == 1
        assert rec.incidents[0] == ("SES-1", "bot-1", "battery_low", 0)  # 1st time → 0 prior

        # Second capture of the same pattern → prior_occurrences = 1.
        _ingest(client, "SES-2")
        assert rec.incidents[-1] == ("SES-2", "bot-1", "battery_low", 1)


def test_resolution_counts_first_terminal_only(tmp_path: Path):
    rec = RecordingTelemetry()
    with TestClient(_app(tmp_path, rec)) as client:
        _ingest(client, "SES-1")
        url = "/api/v2/sessions/SES-1/resolution"

        # open -> resolved : first terminal transition, counts.
        assert client.put(url, json={"status": "resolved"}).status_code == 200
        assert rec.resolutions == ["resolved"]

        # resolved -> resolved : terminal→terminal, no new count.
        client.put(url, json={"status": "resolved"})
        assert rec.resolutions == ["resolved"]

        # resolved -> open : back to non-terminal, no count.
        client.put(url, json={"status": "open"})
        assert rec.resolutions == ["resolved"]

        # open -> wont_fix : a new first-terminal transition, counts.
        client.put(url, json={"status": "wont_fix"})
        assert rec.resolutions == ["resolved", "wont_fix"]


def test_telemetry_shutdown_called(tmp_path: Path):
    rec = RecordingTelemetry()
    with TestClient(_app(tmp_path, rec)):
        pass
    assert rec.shutdowns == 1


def test_default_is_noop_when_unconfigured(tmp_path: Path, monkeypatch):
    # No MD_OTEL_ENDPOINT → build_app builds a no-op Telemetry; ingest works.
    monkeypatch.delenv("MD_OTEL_ENDPOINT", raising=False)
    app = build_app(tmp_path / "sessions", tmp_path / "db.sqlite3")
    with TestClient(app) as client:
        assert _ingest(client, "SES-1").status_code == 200
