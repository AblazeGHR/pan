"""Offline inventory, dry-run planning and explicit migration apply.

Inventory uses the standard library; apply validates via WP01 contracts.
This package must not be used by the running Pan service. The scanned root is
always read-only; apply publishes an offline bundle outside it.
"""

from .inventory import (
    MAPPING_VERSION,
    TOOL_SCHEMA,
    RealProfileError,
    build_inventory,
)

__all__ = [
    "MAPPING_VERSION",
    "TOOL_SCHEMA",
    "RealProfileError",
    "build_inventory",
]
