"""Read-only inventory and dry-run planning for the Job final-model migration.

This package deliberately imports only the Python standard library. It must not
be used by the running Pan service, and it never writes to the scanned root.
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
