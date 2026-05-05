"""Anomaly detectors. One file per detector.

Each detector exposes:
  - a dataclass for the anomaly event it produces
  - a class with one or more `update_*()` methods + an `on_anomaly` callback
"""
