"""rclpy bridge: subscribes to configured topics and serializes incoming
messages straight to bytes for the ring buffer.

We deliberately do NOT deserialize. The agent's job is to capture, not to
understand. The browser decodes.

Importing rclpy is deferred so unit tests of ring_buffer / mcap_writer don't
require a ROS 2 install.
"""

from __future__ import annotations

import importlib
import logging
import time
from typing import Callable

from .config import AgentConfig, TopicConfig
from .rate_limiter import RateLimiter
from .ring_buffer import BufferedMessage, RingBuffer

log = logging.getLogger(__name__)


def _resolve_msg_type(type_str: str):
    """Resolve a ROS 2 type string like 'sensor_msgs/msg/CompressedImage' to a class."""
    parts = type_str.split("/")
    if len(parts) != 3:
        raise ValueError(
            f"Expected ROS type 'pkg/msg/Type', got {type_str!r}"
        )
    pkg, sub, name = parts
    module = importlib.import_module(f"{pkg}.{sub}")
    return getattr(module, name)


class RosBridge:
    """Wraps an rclpy Node with one subscription per configured topic."""

    def __init__(
        self,
        config: AgentConfig,
        buffer: RingBuffer,
        message_callbacks: dict[str, Callable] | None = None,
    ) -> None:
        # Defer rclpy import — only required at runtime.
        import rclpy
        from rclpy.node import Node
        from rclpy.serialization import serialize_message

        self._rclpy = rclpy
        self._serialize = serialize_message
        self._buffer = buffer
        self._config = config
        # topic name -> callback(msg, ts_ns). Topic must be in `config.topics`.
        self._callbacks: dict[str, Callable] = message_callbacks or {}
        self._rate_limiter = RateLimiter()
        # Pre-register per-topic windows on the buffer so first-message latency
        # doesn't pay for it.
        for topic in config.topics:
            if topic.ring_seconds is not None:
                buffer.configure_topic(topic.name, topic.ring_seconds)

        if not rclpy.ok():
            rclpy.init()

        self._node: Node = rclpy.create_node("missiondebug_agent")
        self._subs = []
        for topic in config.topics:
            self._subscribe(topic)

    def _subscribe(self, topic: TopicConfig) -> None:
        try:
            msg_cls = _resolve_msg_type(topic.type)
        except (ModuleNotFoundError, AttributeError, ValueError) as err:
            # One unresolvable topic must not take down the whole agent. This
            # happens when a message package isn't installed on the robot (e.g.
            # a manipulator preset's moveit_msgs on a robot without MoveIt).
            # Skip it with a warning; capture everything else and stay up.
            log.warning(
                "Skipping topic %s: cannot resolve message type %r (%s). "
                "Is the message package installed on this robot?",
                topic.name, topic.type, err,
            )
            return
        cb_for_topic = self._callbacks.get(topic.name)
        divisor = topic.rate_divisor

        def cb(
            msg,
            _topic_name=topic.name,
            _user_cb=cb_for_topic,
            _divisor=divisor,
        ):
            if not self._rate_limiter.should_keep(_topic_name, _divisor):
                return
            try:
                payload = self._serialize(msg)
                ts = time.monotonic_ns()
                self._buffer.append(
                    BufferedMessage(
                        timestamp_ns=ts,
                        wall_ns=time.time_ns(),
                        topic=_topic_name,
                        payload=payload,
                    )
                )
                if _user_cb is not None:
                    _user_cb(msg, ts)
            except Exception:
                log.exception("Failed to buffer message on %s", _topic_name)
                raise

        sub = self._node.create_subscription(msg_cls, topic.name, cb, 10)
        self._subs.append(sub)
        rate_note = f", every {divisor}th" if divisor > 1 else ""
        ring_note = f", ring={topic.ring_seconds}s" if topic.ring_seconds else ""
        log.info("Subscribed to %s [%s]%s%s", topic.name, topic.type, rate_note, ring_note)

    def spin(self) -> None:
        """Blocks until shutdown."""
        self._rclpy.spin(self._node)

    def shutdown(self) -> None:
        try:
            self._node.destroy_node()
        finally:
            if self._rclpy.ok():
                self._rclpy.shutdown()
