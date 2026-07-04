"""Configured-topic health: is everything this agent is supposed to capture
actually capturable right now?

Compares the capture config against the live ROS graph (the same warm
discovery cache GET /topics uses) and buckets each configured topic:

  unresolvable — message package not built/sourced here; the subscription
                 was (or would be) silently skipped. The px4_msgs case.
  missing      — type resolves but the topic isn't on the graph at all.
  silent       — on the graph but zero publishers (a ghost: often only our
                 own capture subscription is keeping the name alive).
  ok           — visible, published, decodable.

The result rides the hub heartbeat so the fleet page can flag a robot whose
config quietly rotted — turning the "empty capture at the worst moment"
failure into a badge you see the same minute. Best-effort by design: any
error or an unsettled scan yields None (no claim) rather than a wrong claim.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_LIST_CAP = 20  # keep heartbeats small on badly broken configs


def compute_topics_health(
    configured: list[tuple[str, str]],
    *,
    scan=None,
    resolve=None,
) -> dict | None:
    """Health summary for the configured capture topics, or None when the
    graph can't be read reliably (no rclpy, scan error, unsettled scan).

    `scan` and `resolve` are injectable for tests: scan() returns the
    discovery dict ({"topics": [...], "settled": bool}); resolve(type_str)
    returns whether the message type imports here.
    """
    if not configured:
        return None
    try:
        if scan is None or resolve is None:
            import rclpy  # noqa: F401  — no ROS -> no health claim

            from .discovery import _is_resolvable, discover_topics

            scan = scan or discover_topics
            resolve = resolve or _is_resolvable

        result = scan()
        if not result.get("settled", False):
            # A partial DDS snapshot would report healthy topics as missing.
            return None
        visible = {t["name"]: t for t in result.get("topics") or []}

        ok = 0
        missing: list[str] = []
        silent: list[str] = []
        unresolvable: list[str] = []
        for name, type_str in configured:
            if type_str and not resolve(type_str):
                unresolvable.append(name)
            elif name not in visible:
                missing.append(name)
            elif visible[name].get("publishers") == 0:
                silent.append(name)
            else:
                ok += 1
        return {
            "ok": ok,
            "missing": sorted(missing)[:_LIST_CAP],
            "silent": sorted(silent)[:_LIST_CAP],
            "unresolvable": sorted(unresolvable)[:_LIST_CAP],
        }
    except Exception as e:
        log.debug("topics-health computation skipped: %s", e)
        return None
