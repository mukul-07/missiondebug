"""Battery-low convenience detector (v1.5).

Just a config sugar: takes a BatteryLowConfig, returns an equivalent
RuleConfig that the existing rule engine handles. Saves the customer
from writing the raw `lt: 0.20, field: percentage` form when the
common case is "the BatteryState percentage dropped below X."
"""

from __future__ import annotations

from ..config import BatteryLowConfig, RuleConfig


def to_rule(cfg: BatteryLowConfig) -> RuleConfig:
    return RuleConfig(
        name=cfg.name,
        topic=cfg.topic,
        field=cfg.field,
        lt=cfg.threshold,
        duration_seconds=cfg.duration_seconds,
        cooldown_seconds=cfg.cooldown_seconds,
    )
