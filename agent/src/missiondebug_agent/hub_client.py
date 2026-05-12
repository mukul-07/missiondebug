"""Optional client that pushes session metadata + heartbeats to a hub.

The agent runs on the robot. When `hub.url` is configured in agent
config, the agent additionally:

  1. POSTs each saved session's metadata to <hub>/api/v1/sessions/ingest
     immediately after the local MCAP is written.
  2. POSTs a heartbeat to <hub>/api/v1/agents/heartbeat every
     `heartbeat_interval_seconds` (default 60s).

Both operations are non-fatal: network errors are logged and retried
with exponential backoff up to 5 minutes, but never block the agent's
real work. Hard Rule 18: standalone mode (no hub configured) must
continue to work unchanged — that's why nothing in this module is
imported at top level by main.py unless `hub.url` is set.

Implementation choices:
  - urllib.request, not requests/httpx — keeps the agent's runtime
    dependency surface tiny. The agent ships in a `.deb` and we don't
    want to pull in an HTTP client just for two POSTs.
  - Thread-based, not asyncio — the agent's main loop is rclpy's
    executor (spinning), not an event loop. Adding asyncio would
    complicate shutdown. One background thread is enough.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


# Reasonable HTTP defaults. The agent posts to a hub on the same LAN
# in the typical deployment; long timeouts just delay shutdown.
_POST_TIMEOUT_S = 5.0


@dataclass
class HubClientConfig:
    """Just the bits of AgentConfig + HubConfig that hub_client needs.

    Decoupled so tests can construct one without depending on the full
    AgentConfig / pydantic chain.
    """

    hub_url: str
    robot_id: str
    agent_url: str | None = None
    auth_token: str | None = None
    agent_version: str | None = None
    subsystem: str | None = None
    heartbeat_interval_seconds: float = 60.0


class HubClient:
    """Background hub-sync client.

    Three responsibilities:
    - Periodic heartbeats on a daemon thread.
    - On-demand session-metadata ingest, called by save_now() right
      after the MCAP is written.
    - Internal retry/backoff for both, surfaced via logs only.
    """

    def __init__(
        self,
        config: HubClientConfig,
        *,
        post_fn=None,  # for tests: injectable HTTP transport
    ) -> None:
        self._cfg = config
        self._post = post_fn or _http_post_json
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Diagnostic counters — useful for tests + a future /healthz field.
        self.heartbeats_sent = 0
        self.heartbeats_failed = 0
        self.sessions_sent = 0
        self.sessions_failed = 0

    # ---- lifecycle -------------------------------------------------

    def start(self) -> None:
        """Start the heartbeat thread. Idempotent — second call is a no-op."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._heartbeat_loop,
            name="missiondebug-hub-heartbeat",
            daemon=True,
        )
        self._thread.start()
        log.info("hub_client started (url=%s, robot=%s)", self._cfg.hub_url, self._cfg.robot_id)

    def stop(self, timeout: float = 2.0) -> None:
        """Signal the heartbeat loop to exit and wait briefly for it."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    # ---- public surface -------------------------------------------

    def report_session(self, payload: dict[str, Any]) -> bool:
        """POST session metadata to the hub. Returns True on success.

        Called synchronously from save_now() after the MCAP file is
        written. Network failure is logged but does not raise — the
        local save still succeeded; the hub will just not see it.
        """
        url = self._cfg.hub_url.rstrip("/") + "/api/v1/sessions/ingest"
        body = self._enrich_session_payload(payload)
        try:
            self._post(url, body, auth_token=self._cfg.auth_token, timeout=_POST_TIMEOUT_S)
            self.sessions_sent += 1
            return True
        except Exception as e:
            self.sessions_failed += 1
            log.warning("hub session-ingest failed: %s", e)
            return False

    # ---- private --------------------------------------------------

    def _enrich_session_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Add robot_id, agent_url, agent_version, subsystem if not present."""
        out = dict(payload)
        out.setdefault("robot_id", self._cfg.robot_id)
        if self._cfg.agent_url:
            out.setdefault("agent_url", self._cfg.agent_url)
        if self._cfg.agent_version:
            out.setdefault("agent_version", self._cfg.agent_version)
        if self._cfg.subsystem:
            out.setdefault("subsystem", self._cfg.subsystem)
        # mcap_url is required by the hub. Construct it if the caller
        # didn't provide one — the agent's own /api/sessions/{id}/mcap
        # endpoint is canonical.
        if "mcap_url" not in out and self._cfg.agent_url and "session_id" in out:
            out["mcap_url"] = (
                self._cfg.agent_url.rstrip("/")
                + f"/api/sessions/{out['session_id']}/mcap"
            )
        return out

    def _heartbeat_loop(self) -> None:
        """Sleep `heartbeat_interval_seconds`, then ping. Retries with
        exponential backoff on failure, capped at 300s. Exits when
        `stop()` is called."""
        url = self._cfg.hub_url.rstrip("/") + "/api/v1/agents/heartbeat"
        backoff = self._cfg.heartbeat_interval_seconds
        while not self._stop.is_set():
            # Wait first so we don't ping immediately on startup — gives
            # the agent time to actually bring up subscriptions.
            if self._stop.wait(timeout=backoff):
                return
            payload: dict[str, Any] = {"robot_id": self._cfg.robot_id}
            if self._cfg.agent_url:
                payload["agent_url"] = self._cfg.agent_url
            if self._cfg.agent_version:
                payload["agent_version"] = self._cfg.agent_version
            try:
                self._post(url, payload, auth_token=self._cfg.auth_token, timeout=_POST_TIMEOUT_S)
                self.heartbeats_sent += 1
                backoff = self._cfg.heartbeat_interval_seconds
            except Exception as e:
                self.heartbeats_failed += 1
                # Cap backoff at 5 minutes — beyond that the hub probably
                # has bigger problems than another retry will solve.
                backoff = min(300.0, backoff * 2)
                log.warning("hub heartbeat failed (retry in %.0fs): %s", backoff, e)


def _http_post_json(
    url: str,
    body: dict[str, Any],
    *,
    auth_token: str | None = None,
    timeout: float = _POST_TIMEOUT_S,
) -> None:
    """Default HTTP transport. Raises urllib.error.URLError on failure.

    Kept module-level (not a HubClient method) so tests can inject an
    alternate transport via post_fn=.
    """
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if auth_token:
        req.add_header("Authorization", f"Bearer {auth_token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        # We don't use the response body. Drain to free the connection.
        resp.read()


def fake_post_factory():
    """Test helper: returns a (recorder, post_fn) tuple where recorder
    is a list of (url, body, auth_token) tuples captured by post_fn.

    Use in tests as:
        recorder, post_fn = fake_post_factory()
        client = HubClient(cfg, post_fn=post_fn)
        ...
        assert len(recorder) == 1
    """
    recorder: list[tuple[str, dict[str, Any], str | None]] = []

    def post_fn(url, body, *, auth_token=None, timeout=None):
        recorder.append((url, dict(body), auth_token))

    return recorder, post_fn
