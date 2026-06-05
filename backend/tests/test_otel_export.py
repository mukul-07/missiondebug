"""v2 OTel — OtelTelemetry actually emits metrics + an incident log record.

Uses in-memory OTel exporters so emission is verified without a live
collector. Skipped entirely if the OpenTelemetry SDK (the ``[otel]`` extra)
isn't installed — the base install must not require it.
"""

from pathlib import Path

import pytest

pytest.importorskip("opentelemetry.sdk.metrics")

from opentelemetry.sdk._logs.export import (  # noqa: E402
    InMemoryLogRecordExporter,
    SimpleLogRecordProcessor,
)
from opentelemetry.sdk.metrics.export import InMemoryMetricReader  # noqa: E402

from missiondebug_backend.db import Db  # noqa: E402
from missiondebug_backend.telemetry import OtelConfig  # noqa: E402


def _otel(db):
    from missiondebug_backend._otel import OtelTelemetry

    reader = InMemoryMetricReader()
    log_exporter = InMemoryLogRecordExporter()
    telemetry = OtelTelemetry(
        db,
        OtelConfig(
            endpoint="http://collector:4318",
            service_name="missiondebug-test",
            public_url="http://hub:8000",
        ),
        metric_reader=reader,
        log_processor=SimpleLogRecordProcessor(log_exporter),
    )
    return telemetry, reader, log_exporter


def _metric_names(data) -> set[str]:
    names: set[str] = set()
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                names.add(m.name)
    return names


def test_counters_and_gauges_export(tmp_path: Path):
    db = Db(tmp_path / "db.sqlite3")
    telemetry, reader, _ = _otel(db)
    try:
        telemetry.record_capture(robot_id="bot-1", subsystem="power", rule="battery_low")
        telemetry.record_resolution(status="resolved")

        names = _metric_names(reader.get_metrics_data())
        # Counters we incremented...
        assert "missiondebug.incidents.captured" in names
        assert "missiondebug.incidents.resolved" in names
        # ...and the observable gauges (collected via callbacks, even on an
        # empty DB — they must not error).
        assert "missiondebug.agents.total" in names
        assert "missiondebug.recurrence.rate" in names
        assert "missiondebug.mttr.days" in names
    finally:
        telemetry.shutdown()


def test_incident_event_has_deeplink_and_recurrence(tmp_path: Path):
    db = Db(tmp_path / "db.sqlite3")
    telemetry, _, log_exporter = _otel(db)
    try:
        telemetry.emit_incident(
            session_id="SES-9",
            robot_id="bot-1",
            subsystem="power",
            rule="battery_low",
            summary="battery dipped",
            prior_occurrences=2,
        )
        logs = log_exporter.get_finished_logs()
        assert len(logs) == 1
        record = logs[0].log_record
        assert record.attributes["session_id"] == "SES-9"
        assert record.attributes["robot_id"] == "bot-1"
        assert record.attributes["rule"] == "battery_low"
        assert record.attributes["url"] == "http://hub:8000/sessions/SES-9"
        assert record.attributes["prior_occurrences"] == 2
        # 2 prior + this one = 3rd occurrence.
        assert "3rd occurrence" in record.body
        assert "battery dipped" in record.body
    finally:
        telemetry.shutdown()
