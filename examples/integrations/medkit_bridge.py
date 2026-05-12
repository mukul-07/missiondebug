#!/usr/bin/env python3
"""Bridge ros2_medkit trigger events to MissionDebug session captures.

medkit's Triggers expose Server-Sent Events (SSE), not outbound webhooks
(as of ros2_medkit 0.x — see https://selfpatch.github.io/ros2_medkit/).
This script subscribes to one or more medkit trigger event streams and
POSTs to MissionDebug's agent save endpoint when an event arrives.

Usage:
    pip install requests sseclient-py
    export MD_AGENT_URL=http://localhost:7000
    export MEDKIT_URL=http://localhost:8080
    python3 medkit_bridge.py \\
        /api/v1/apps/temp_sensor/triggers/trig_1/events \\
        /api/v1/apps/lidar/triggers/trig_2/events

Each positional argument is a trigger event-source path relative to
MEDKIT_URL. The script connects to all of them concurrently and forwards
each event to MissionDebug with a label of "medkit:<trigger_path>".

Reconnects on network errors with exponential backoff up to 60s.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from typing import Iterable
from urllib.parse import urljoin

try:
    import requests
    from sseclient import SSEClient
except ImportError:
    print("Install dependencies first: pip install requests sseclient-py", file=sys.stderr)
    sys.exit(1)

log = logging.getLogger("medkit-bridge")


def watch_one(medkit_url: str, event_path: str, agent_url: str) -> None:
    """Subscribe to one trigger event stream and forward each event to the agent."""
    stream_url = urljoin(medkit_url + "/", event_path.lstrip("/"))
    label = f"medkit:{event_path.strip('/').replace('/', '-')}"

    backoff = 1.0
    while True:
        try:
            log.info("Subscribing to %s", stream_url)
            resp = requests.get(stream_url, stream=True, timeout=(10, None))
            resp.raise_for_status()
            client = SSEClient(resp)
            for event in client.events():
                if not event.data:
                    continue
                log.info("Trigger fired on %s — forwarding to MissionDebug", event_path)
                _post_save(agent_url, label)
                backoff = 1.0
        except Exception:
            log.exception("Stream %s errored; retrying in %.0fs", event_path, backoff)
            time.sleep(backoff)
            backoff = min(60.0, backoff * 2)


def _post_save(agent_url: str, label: str) -> None:
    """POST to the MissionDebug agent's save endpoint."""
    try:
        r = requests.post(
            urljoin(agent_url + "/", "sessions/save"),
            json={"label": label},
            timeout=5,
        )
        if r.ok:
            log.info("Saved session: %s", r.json().get("session_id"))
        else:
            log.warning("Agent returned %s: %s", r.status_code, r.text[:200])
    except Exception:
        log.exception("Agent POST failed")


def main(args: Iterable[str]) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    paths = list(args)
    if not paths:
        print(__doc__, file=sys.stderr)
        return 2

    medkit_url = os.environ.get("MEDKIT_URL", "http://localhost:8080")
    agent_url = os.environ.get("MD_AGENT_URL", "http://localhost:7000")
    log.info("medkit=%s, agent=%s, %d stream(s)", medkit_url, agent_url, len(paths))

    threads = [
        threading.Thread(
            target=watch_one,
            args=(medkit_url, p, agent_url),
            daemon=True,
            name=f"sse-{p}",
        )
        for p in paths
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
