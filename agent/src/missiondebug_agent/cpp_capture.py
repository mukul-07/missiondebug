"""Adapter that runs the C++ capture module (missiondebug_capture) in place of
the Python RosBridge + RingBuffer, when the compiled module is importable.

The agent's hot path (subscribe -> serialize -> buffer) is the CPU cost; the C++
module does that natively (~2x cheaper, measured). Everything else stays Python:
config, the control API, detectors, MCAP writing. This adapter presents the same
surface the rest of the agent already uses:

  - a `snapshot()` that returns BufferedMessage objects (so save_now / write_session
    are unchanged),
  - detector callbacks fed the SAME deserialized message shape they get today.

Selection is at runtime: `build_capture()` returns an adapter when the module
imports and is not disabled, else None (the caller falls back to the Python
RosBridge + RingBuffer). Set MD_CAPTURE=python to force the Python path.
"""

from __future__ import annotations

import logging
import os
from typing import Callable

from .config import AgentConfig
from .ring_buffer import BufferedMessage

log = logging.getLogger(__name__)


def cpp_available() -> bool:
    """True if the compiled C++ capture module can be imported and is enabled."""
    if os.environ.get("MD_CAPTURE", "").lower() == "python":
        return False
    try:
        import missiondebug_capture  # noqa: F401
        return True
    except Exception:
        return False


class CppCaptureAdapter:
    """Wraps missiondebug_capture.Capture to look like RosBridge + RingBuffer.

    `message_callbacks` is the same {topic: fn(msg, ts_ns)} map main.py builds
    for the Python bridge. Topics in it are marked as detector topics on the C++
    side; the C++ module delivers their raw bytes back here, we deserialize
    (only those low-rate topics) and call the existing callback with the typed
    message. High-rate buffer-only topics never cross into Python.
    """

    def __init__(
        self,
        config: AgentConfig,
        message_callbacks: dict[str, Callable] | None = None,
    ) -> None:
        import missiondebug_capture as mc
        from rclpy.serialization import deserialize_message

        self._mc = mc
        self._deserialize = deserialize_message
        self._callbacks = message_callbacks or {}
        self._config = config
        # Resolve each detector topic's message class once, for deserialize.
        from .ros_bridge import _resolve_msg_type
        self._msg_cls: dict[str, type] = {}

        detector_topics = set(self._callbacks.keys())
        specs = []
        for t in config.topics:
            spec = mc.TopicSpec()
            spec.name = t.name
            spec.type = t.type
            spec.reliability = t.reliability or ""
            spec.queue_depth = t.queue_depth
            spec.rate_divisor = t.rate_divisor
            spec.ring_seconds = t.ring_seconds or 0.0
            spec.is_detector = t.name in detector_topics
            specs.append(spec)
            if spec.is_detector:
                try:
                    self._msg_cls[t.name] = _resolve_msg_type(t.type)
                except Exception:
                    # An unresolvable detector topic: skip the callback for it,
                    # capture still works (mirrors the Python bridge's skip).
                    log.warning(
                        "C++ capture: cannot resolve %r for detector topic %s; "
                        "buffering it but its detector will not fire.",
                        t.type, t.name,
                    )

        self._cap = mc.Capture(
            topics=specs,
            buffer_seconds=config.buffer_seconds,
            max_total_bytes=config.max_total_bytes or 0,
        )
        self._cap.set_detector_callback(self._on_detector_message)

    def _on_detector_message(self, topic: str, payload: bytes, ts_ns: int) -> None:
        """Called from the C++ spin thread (GIL held) for each detector-topic
        message. Deserialize the bytes and fan out to the existing callback."""
        cb = self._callbacks.get(topic)
        cls = self._msg_cls.get(topic)
        if cb is None or cls is None:
            return
        try:
            msg = self._deserialize(bytes(payload), cls)
            cb(msg, ts_ns)
        except Exception:
            log.exception("C++ capture: detector callback failed on %s", topic)

    def start(self) -> None:
        self._cap.start()
        log.info("Capture: using the C++ module (missiondebug_capture).")

    def stop(self) -> None:
        self._cap.stop()

    def __len__(self) -> int:
        return self._cap.buffer_size()

    def snapshot(self) -> list[BufferedMessage]:
        """Return the buffered messages as BufferedMessage objects, the shape
        save_now / write_session consume. The C++ snapshot is a list of
        (topic, ts_ns, wall_ns, bytes)."""
        return [
            BufferedMessage(
                timestamp_ns=ts_ns,
                wall_ns=wall_ns,
                topic=topic,
                payload=payload,
            )
            for (topic, ts_ns, wall_ns, payload) in self._cap.snapshot()
        ]


def build_capture(
    config: AgentConfig,
    message_callbacks: dict[str, Callable] | None = None,
) -> CppCaptureAdapter | None:
    """Return a C++ capture adapter, or None if the module is unavailable /
    disabled (caller falls back to the Python RosBridge + RingBuffer)."""
    if not cpp_available():
        return None
    try:
        return CppCaptureAdapter(config, message_callbacks)
    except Exception:
        log.exception(
            "C++ capture module present but failed to initialize; "
            "falling back to the Python capture path."
        )
        return None
