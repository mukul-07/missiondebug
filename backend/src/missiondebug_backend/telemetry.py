"""Optional OpenTelemetry export for the hub — opt-in, self-hosted.

Active only when ``MD_OTEL_ENDPOINT`` is set. Standalone / air-gapped
installs (no endpoint) get a no-op ``Telemetry`` and behave exactly as
before (Hard Rule 18). The collector is the operator's own, on their
network — there is no MissionDebug cloud (HR20). Only incident *metadata*
is exported (robot_id, rule, subsystem, counts, deep-link) — never MCAP
bytes, camera frames, or PII (HR26). The OpenTelemetry SDK is an optional
dependency (the ``[otel]`` extra), so the base install stays lightweight
(HR25): nothing here imports it unless an endpoint is configured.

What it emits (to the operator's OTLP collector → their Grafana / Datadog
/ alert routing):
  - metrics: ``missiondebug.incidents.captured`` / ``.resolved`` counters,
    plus gauges for agents reporting, open incidents, recurrence rate, MTTR.
  - logs: one structured record per captured incident, with a deep-link
    back to the session and a "Nth occurrence" hint — routable to Slack /
    PagerDuty by the operator's existing pipeline.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


def rule_from_label(label: str | None) -> str | None:
    """Extract the bare rule name from the agent's label convention.

    "anomaly:battery_low" -> "battery_low"; "manual" / None stay as-is.
    Keeps the metric/event ``rule`` attribute low-cardinality and readable.
    """
    if not label:
        return None
    if label.startswith("anomaly:"):
        return label[len("anomaly:"):] or None
    return label


@dataclass
class OtelConfig:
    endpoint: str | None = None  # OTLP/HTTP base, e.g. http://collector:4318
    headers: dict[str, str] = field(default_factory=dict)
    service_name: str = "missiondebug"
    public_url: str = ""  # hub base URL for deep-links in incident events

    @classmethod
    def from_env(cls) -> OtelConfig:
        endpoint = os.environ.get("MD_OTEL_ENDPOINT", "").strip() or None
        headers: dict[str, str] = {}
        for pair in os.environ.get("MD_OTEL_HEADERS", "").split(","):
            pair = pair.strip()
            if "=" in pair:
                k, v = pair.split("=", 1)
                headers[k.strip()] = v.strip()
        return cls(
            endpoint=endpoint,
            headers=headers,
            service_name=(os.environ.get("MD_OTEL_SERVICE_NAME", "").strip() or "missiondebug"),
            public_url=os.environ.get("MD_HUB_PUBLIC_URL", "").strip().rstrip("/"),
        )


class Telemetry:
    """No-op telemetry. Callers always hold a ``Telemetry``; the no-op
    variant keeps the call sites branch-free. ``OtelTelemetry`` (in
    ``_otel``) overrides the record/emit methods when export is enabled."""

    enabled = False

    def record_capture(self, *, robot_id: str, subsystem: str | None, rule: str | None) -> None:
        pass

    def record_resolution(self, *, status: str) -> None:
        pass

    def emit_incident(
        self,
        *,
        session_id: str,
        robot_id: str,
        subsystem: str | None,
        rule: str | None,
        summary: str | None,
        prior_occurrences: int | None = None,
    ) -> None:
        pass

    def shutdown(self) -> None:
        pass


def build_telemetry(db, config: OtelConfig | None = None) -> Telemetry:
    """Return an ``OtelTelemetry`` when an endpoint is configured and the SDK
    is importable; otherwise a no-op. Never raises — telemetry must never
    take the hub down."""
    config = config or OtelConfig.from_env()
    if not config.endpoint:
        return Telemetry()
    try:
        from ._otel import OtelTelemetry
    except Exception as e:  # SDK not installed
        log.warning(
            "MD_OTEL_ENDPOINT is set but the OpenTelemetry SDK is not installed "
            "(%s). Install with: pip install 'missiondebug-backend[otel]'. "
            "Continuing without telemetry export.",
            e,
        )
        return Telemetry()
    try:
        telemetry = OtelTelemetry(db, config)
        log.info(
            "OpenTelemetry export enabled -> %s (service=%s)",
            config.endpoint,
            config.service_name,
        )
        return telemetry
    except Exception:
        log.exception("Failed to initialise OpenTelemetry export; continuing without it.")
        return Telemetry()
