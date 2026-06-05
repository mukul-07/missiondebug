"""Real OpenTelemetry implementation.

Imported lazily by ``telemetry.build_telemetry`` only when
``MD_OTEL_ENDPOINT`` is set and the ``[otel]`` extra is installed — so the
OpenTelemetry dependency surface never loads for standalone installs.

Gauges reuse the same aggregation the fleet dashboard uses (single source
of truth — no drift between what the UI shows and what Grafana shows).
"""

from __future__ import annotations

import logging
import time

from opentelemetry._logs import SeverityNumber
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.metrics import Observation
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

from .db import Db
from .routes.agents import _classify
from .routes.fleet_stats import (
    _mttr,
    _now_ms,
    _recurrence,
    _resolution_breakdown,
)
from .telemetry import OtelConfig, Telemetry

log = logging.getLogger(__name__)

_WINDOW_DAYS = 30
_MS_PER_DAY = 86_400_000
_TERMINAL = {"resolved", "duplicate", "wont_fix"}


def _metrics_url(endpoint: str) -> str:
    return endpoint.rstrip("/") + "/v1/metrics"


def _logs_url(endpoint: str) -> str:
    return endpoint.rstrip("/") + "/v1/logs"


def _attrs(*, robot_id: str, subsystem: str | None, rule: str | None) -> dict:
    a: dict[str, object] = {"robot_id": robot_id}
    if subsystem:
        a["subsystem"] = subsystem
    if rule:
        a["rule"] = rule
    return a


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


class OtelTelemetry(Telemetry):
    """Exports incident metrics + events over OTLP/HTTP.

    ``metric_reader`` / ``log_processor`` can be injected for tests (e.g. an
    in-memory reader) so emission is verifiable without a live collector.
    """

    enabled = True

    def __init__(self, db: Db, config: OtelConfig, *, metric_reader=None, log_processor=None):
        self._db = db
        self._config = config
        resource = Resource.create({"service.name": config.service_name})

        reader = metric_reader or PeriodicExportingMetricReader(
            OTLPMetricExporter(
                endpoint=_metrics_url(config.endpoint or ""),
                headers=config.headers or None,
            ),
            export_interval_millis=15_000,
        )
        self._meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
        meter = self._meter_provider.get_meter("missiondebug.hub")

        self._captured = meter.create_counter(
            "missiondebug.incidents.captured",
            unit="1",
            description="Incidents captured (sessions ingested to the hub).",
        )
        self._resolved = meter.create_counter(
            "missiondebug.incidents.resolved",
            unit="1",
            description="Incidents moved into a terminal resolution status (first transition).",
        )
        meter.create_observable_gauge(
            "missiondebug.agents.reporting",
            callbacks=[self._obs_agents_reporting],
            unit="1",
            description="Agents that heartbeated within the stale window.",
        )
        meter.create_observable_gauge(
            "missiondebug.agents.total",
            callbacks=[self._obs_agents_total],
            unit="1",
            description="Agents known to the hub.",
        )
        meter.create_observable_gauge(
            "missiondebug.incidents.open",
            callbacks=[self._obs_open],
            unit="1",
            description="Open or investigating incidents in the last 30 days.",
        )
        meter.create_observable_gauge(
            "missiondebug.recurrence.rate",
            callbacks=[self._obs_recurrence],
            unit="1",
            description="Fraction of last-30-day incidents marked duplicate.",
        )
        meter.create_observable_gauge(
            "missiondebug.mttr.days",
            callbacks=[self._obs_mttr],
            unit="d",
            description="Mean time to first resolution (days), last 30 days.",
        )

        processor = log_processor or BatchLogRecordProcessor(
            OTLPLogExporter(
                endpoint=_logs_url(config.endpoint or ""),
                headers=config.headers or None,
            )
        )
        self._logger_provider = LoggerProvider(resource=resource)
        self._logger_provider.add_log_record_processor(processor)
        self._logger = self._logger_provider.get_logger("missiondebug.incidents")

    # ---- counters -------------------------------------------------------

    def record_capture(self, *, robot_id, subsystem, rule):
        self._captured.add(1, _attrs(robot_id=robot_id, subsystem=subsystem, rule=rule))

    def record_resolution(self, *, status):
        self._resolved.add(1, {"status": status})

    # ---- incident event (structured log record) -------------------------

    def emit_incident(
        self, *, session_id, robot_id, subsystem, rule, summary, prior_occurrences=None
    ):
        attrs = _attrs(robot_id=robot_id, subsystem=subsystem, rule=rule)
        attrs["session_id"] = session_id
        if self._config.public_url:
            attrs["url"] = f"{self._config.public_url}/sessions/{session_id}"
        if prior_occurrences is not None:
            attrs["prior_occurrences"] = prior_occurrences
        recur = (
            f" — {_ordinal((prior_occurrences or 0) + 1)} occurrence of this pattern"
            if prior_occurrences
            else ""
        )
        body = f"Incident on {robot_id}: {rule or 'manual capture'}{recur}."
        if summary:
            body = f"{body} {summary}"
        now_ns = time.time_ns()
        # Kwargs form of Logger.emit — stable across recent OTel and avoids
        # constructing a LogRecord (which moved out of the public SDK API).
        self._logger.emit(
            timestamp=now_ns,
            observed_timestamp=now_ns,
            severity_text="WARN",
            severity_number=SeverityNumber.WARN,
            body=body,
            attributes=attrs,
        )

    # ---- lifecycle ------------------------------------------------------

    def shutdown(self):
        for provider in (self._meter_provider, self._logger_provider):
            try:
                provider.shutdown()
            except Exception:
                log.debug("OTel provider shutdown error", exc_info=True)

    # ---- gauge callbacks (reuse the dashboard's aggregation) ------------

    def _windowed_rows(self):
        now = _now_ms()
        return self._db.list_sessions_in_window(
            started_at_gte=now - _WINDOW_DAYS * _MS_PER_DAY,
            started_at_lt=now,
        )

    def _obs_agents_reporting(self, _options):
        now = _now_ms()
        n = sum(1 for a in self._db.list_agents() if _classify(a, now)[0] in ("healthy", "stale"))
        return [Observation(n)]

    def _obs_agents_total(self, _options):
        return [Observation(len(self._db.list_agents()))]

    def _obs_open(self, _options):
        b = _resolution_breakdown(self._windowed_rows())
        return [Observation(b["open"] + b["investigating"])]

    def _obs_recurrence(self, _options):
        rate = _recurrence(self._windowed_rows())["recurrence_rate"]
        return [Observation(rate if rate is not None else 0.0)]

    def _obs_mttr(self, _options):
        ms, _n = _mttr(self._windowed_rows())
        return [Observation((ms / _MS_PER_DAY) if ms is not None else 0.0)]
