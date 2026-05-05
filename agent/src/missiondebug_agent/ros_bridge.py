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

    def __init__(self, config: AgentConfig, buffer: RingBuffer,
                 cmd_vel_callback: Callable | None = None) -> None:
        # Defer rclpy import — only required at runtime.
        import rclpy
        from rclpy.node import Node
        from rclpy.serialization import serialize_message

        self._rclpy = rclpy
        self._serialize = serialize_message
        self._buffer = buffer
        self._config = config
        self._cmd_vel_callback = cmd_vel_callback

        if not rclpy.ok():
            rclpy.init()

        self._node: Node = rclpy.create_node("missiondebug_agent")
        self._subs = []
        for topic in config.topics:
            self._subscribe(topic)

    def _subscribe(self, topic: TopicConfig) -> None:
        msg_cls = _resolve_msg_type(topic.type)

        def cb(msg, _topic_name=topic.name, _is_cmd_vel=topic.name == "/cmd_vel"):
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
                if _is_cmd_vel and self._cmd_vel_callback is not None:
                    self._cmd_vel_callback(msg, ts)
            except Exception:
                log.exception("Failed to buffer message on %s", _topic_name)
                raise

        sub = self._node.create_subscription(msg_cls, topic.name, cb, 10)
        self._subs.append(sub)
        log.info("Subscribed to %s [%s]", topic.name, topic.type)

    def spin(self) -> None:
        """Blocks until shutdown."""
        self._rclpy.spin(self._node)

    def shutdown(self) -> None:
        try:
            self._node.destroy_node()
        finally:
            if self._rclpy.ok():
                self._rclpy.shutdown()
