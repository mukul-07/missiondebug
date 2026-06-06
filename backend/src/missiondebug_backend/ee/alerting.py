# MissionDebug Enterprise Edition — Commercial License, NOT MIT.
# Part of the paid Fleet/Enterprise tiers. Source is visible for evaluation and
# audit; commercial/production use requires a paid license + key. See ee/LICENSE
# and LICENSING.md. Copyright (c) 2026 MissionDebug. All rights reserved.
"""Webhook alerting for the hub (v2 Phase 6) — opt-in, self-hosted.

When a robot captures an incident, the hub can POST a notification to the
operator's Slack / PagerDuty / generic webhook so on-call hears about it
without watching the dashboard. Like telemetry, this is:

  * Opt-in — inert unless at least one destination env var is set
    (HR18: standalone installs unaffected).
  * Self-hosted — the destinations are the operator's own (their Slack
    workspace, their PagerDuty). No MissionDebug cloud (HR20).
  * Metadata-only — rule, robot_id, subsystem, a one-line summary, a
    deep-link. Never MCAP bytes / camera frames / PII (HR26).

Delivery is best-effort and non-blocking: dispatched on a daemon thread so
a slow or unreachable webhook never adds latency to (or fails) the ingest
request. Each destination is attempted independently — one failing does
not stop the others. A per-(rule, robot) cooldown collapses a flapping
detector into at most one alert per window.

Destinations (env):
  MD_ALERT_SLACK_WEBHOOK         Slack incoming-webhook URL → {"text": …}
  MD_ALERT_PAGERDUTY_ROUTING_KEY PagerDuty Events API v2 routing key
  MD_ALERT_WEBHOOK_URL           generic JSON webhook (full incident payload)
  MD_ALERT_COOLDOWN_S            per-(rule,robot) cooldown, default 300s
  MD_HUB_PUBLIC_URL              hub base URL, for the deep-link back
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# A post function: (url, json_body) -> HTTP status code. Raises on transport
# failure. Injectable so tests assert payloads without real HTTP.
PostFn = Callable[[str, dict], int]

_PAGERDUTY_ENQUEUE = "https://events.pagerduty.com/v2/enqueue"


@dataclass
class AlertEvent:
    """The bounded, metadata-only payload an alert is built from."""

    session_id: str
    robot_id: str
    subsystem: str | None
    rule: str | None
    summary: str | None
    prior_occurrences: int | None = None

    @property
    def occurrence(self) -> int | None:
        """1-based occurrence number for this pattern, if known."""
        if self.prior_occurrences is None:
            return None
        return self.prior_occurrences + 1

    def summary_line(self) -> str:
        """First non-empty line of the structured summary (alerts stay terse)."""
        if not self.summary:
            return ""
        for line in self.summary.splitlines():
            line = line.strip()
            if line:
                return line
        return ""


@dataclass
class AlertDelivery:
    destination: str
    ok: bool
    detail: str  # status code as str, "throttled", or the error message


@dataclass
class AlertConfig:
    slack_webhook: str | None = None
    pagerduty_routing_key: str | None = None
    generic_webhook: str | None = None
    cooldown_s: float = 300.0
    public_url: str = ""
    timeout_s: float = 5.0
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> AlertConfig:
        def _val(key: str) -> str | None:
            return os.environ.get(key, "").strip() or None

        cooldown_raw = os.environ.get("MD_ALERT_COOLDOWN_S", "").strip()
        try:
            cooldown = float(cooldown_raw) if cooldown_raw else 300.0
        except ValueError:
            cooldown = 300.0
        return cls(
            slack_webhook=_val("MD_ALERT_SLACK_WEBHOOK"),
            pagerduty_routing_key=_val("MD_ALERT_PAGERDUTY_ROUTING_KEY"),
            generic_webhook=_val("MD_ALERT_WEBHOOK_URL"),
            cooldown_s=cooldown,
            public_url=os.environ.get("MD_HUB_PUBLIC_URL", "").strip().rstrip("/"),
        )

    @property
    def any_configured(self) -> bool:
        return bool(
            self.slack_webhook or self.pagerduty_routing_key or self.generic_webhook
        )


# ---- payload builders (pure, independently testable) -------------------


def _deep_link(public_url: str, session_id: str) -> str:
    if not public_url:
        return ""
    return f"{public_url}/sessions/{session_id}"


def slack_payload(event: AlertEvent, public_url: str) -> dict:
    rule = event.rule or "manual capture"
    where = f" on *{event.robot_id}*"
    if event.subsystem:
        where += f" ({event.subsystem})"
    occ = ""
    if event.occurrence and event.occurrence > 1:
        occ = f" — {_ordinal(event.occurrence)} occurrence"
    line = event.summary_line()
    detail = f"\n{line}" if line else ""
    link = _deep_link(public_url, event.session_id)
    link_md = f"\n<{link}|View in MissionDebug>" if link else ""
    return {
        "text": f":rotating_light: *MissionDebug incident* — `{rule}`{where}{occ}.{detail}{link_md}"
    }


def pagerduty_payload(event: AlertEvent, routing_key: str, public_url: str) -> dict:
    rule = event.rule or "manual capture"
    summary = f"MissionDebug: {rule} on {event.robot_id}"
    if event.subsystem:
        summary += f" ({event.subsystem})"
    link = _deep_link(public_url, event.session_id)
    payload: dict = {
        "routing_key": routing_key,
        "event_action": "trigger",
        # Stable key so PagerDuty de-dupes a flapping rule on one robot into
        # a single alert (complements our own cooldown).
        "dedup_key": f"missiondebug-{rule}-{event.robot_id}",
        "payload": {
            "summary": summary,
            "source": event.robot_id,
            "severity": "warning",
            "custom_details": {
                "session_id": event.session_id,
                "rule": rule,
                "subsystem": event.subsystem,
                "occurrence": event.occurrence,
                "summary": event.summary_line(),
            },
        },
    }
    if link:
        payload["links"] = [{"href": link, "text": "View in MissionDebug"}]
    return payload


def generic_payload(event: AlertEvent, public_url: str) -> dict:
    return {
        "event": "incident.captured",
        "session_id": event.session_id,
        "robot_id": event.robot_id,
        "subsystem": event.subsystem,
        "rule": event.rule,
        "summary": event.summary_line(),
        "prior_occurrences": event.prior_occurrences,
        "occurrence": event.occurrence,
        "url": _deep_link(public_url, event.session_id),
    }


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


# ---- the alerter -------------------------------------------------------


class Alerter:
    """No-op alerter. Callers always hold one; the no-op keeps call sites
    branch-free (mirrors Telemetry). ``WebhookAlerter`` overrides."""

    enabled = False

    def alert_capture(self, event: AlertEvent) -> list[AlertDelivery]:
        return []

    def alert_capture_in_background(self, event: AlertEvent) -> None:
        pass


class WebhookAlerter(Alerter):
    enabled = True

    def __init__(self, config: AlertConfig, post_fn: PostFn | None = None) -> None:
        self._cfg = config
        self._post = post_fn or _default_post(config)
        self._last_sent: dict[str, float] = {}
        self._lock = threading.Lock()

    def _throttled(self, key: str) -> bool:
        """True if an alert for `key` went out within the cooldown window."""
        if self._cfg.cooldown_s <= 0:
            return False
        now = time.monotonic()
        with self._lock:
            last = self._last_sent.get(key)
            if last is not None and (now - last) < self._cfg.cooldown_s:
                return True
            self._last_sent[key] = now
            return False

    def _targets(self, event: AlertEvent) -> list[tuple[str, str, dict]]:
        cfg = self._cfg
        out: list[tuple[str, str, dict]] = []
        if cfg.slack_webhook:
            out.append(("slack", cfg.slack_webhook, slack_payload(event, cfg.public_url)))
        if cfg.pagerduty_routing_key:
            out.append((
                "pagerduty",
                _PAGERDUTY_ENQUEUE,
                pagerduty_payload(event, cfg.pagerduty_routing_key, cfg.public_url),
            ))
        if cfg.generic_webhook:
            out.append(("webhook", cfg.generic_webhook, generic_payload(event, cfg.public_url)))
        return out

    def alert_capture(self, event: AlertEvent) -> list[AlertDelivery]:
        """Dispatch to every configured destination. Each is independent —
        one failing does not stop the others. Returns one delivery record
        per destination (or a single 'throttled' record)."""
        key = f"capture:{event.rule or 'manual'}:{event.robot_id}"
        if self._throttled(key):
            return [AlertDelivery(destination="*", ok=True, detail="throttled")]

        deliveries: list[AlertDelivery] = []
        for name, url, body in self._targets(event):
            try:
                status = self._post(url, body)
                ok = 200 <= status < 300
                deliveries.append(AlertDelivery(name, ok, str(status)))
                if not ok:
                    log.warning("Alert to %s returned HTTP %s", name, status)
            except Exception as e:  # transport error — never propagate
                deliveries.append(AlertDelivery(name, False, str(e)))
                log.warning("Alert to %s failed: %s", name, e)
        return deliveries

    def alert_capture_in_background(self, event: AlertEvent) -> None:
        """Fire-and-forget on a daemon thread so ingest never waits on a
        webhook round-trip."""
        t = threading.Thread(
            target=self._safe_alert, args=(event,), name="alert-capture", daemon=True
        )
        t.start()

    def _safe_alert(self, event: AlertEvent) -> None:
        try:
            self.alert_capture(event)
        except Exception:  # pragma: no cover - defensive
            log.exception("Alert dispatch crashed")


def _default_post(config: AlertConfig) -> PostFn:
    def post(url: str, body: dict) -> int:
        import httpx  # core dep; imported lazily to keep the no-op path clean

        r = httpx.post(url, json=body, timeout=config.timeout_s, headers=config.headers)
        return r.status_code

    return post


def build_alerter(config: AlertConfig | None = None) -> Alerter:
    """Return a ``WebhookAlerter`` when any destination is configured,
    otherwise the no-op. Never raises — alerting must not take the hub down."""
    config = config or AlertConfig.from_env()
    if not config.any_configured:
        return Alerter()
    dests = [
        n
        for n, on in (
            ("slack", config.slack_webhook),
            ("pagerduty", config.pagerduty_routing_key),
            ("webhook", config.generic_webhook),
        )
        if on
    ]
    log.info("Webhook alerting enabled -> %s (cooldown %.0fs)", ", ".join(dests), config.cooldown_s)
    return WebhookAlerter(config)
