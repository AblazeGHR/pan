"""Side-effect-free refusal shared by removed legacy execution entrypoints.

Keep this separate from owner fencing: even a profile that has never activated
the canonical owner must not recreate a legacy Job, Run, receipt or lease.
"""


class RetiredEntryPoint(AttributeError):
    """A removed Python entrypoint, with an explicit retirement diagnostic."""


def missing(name, retired_names):
    if name in retired_names:
        raise RetiredEntryPoint(
            f"Legacy entrypoint {name} retired; use canonical FinalService/FinalRuntime/CreatorRuntime")
    raise AttributeError(name)


async def reject_execution(*args, **kwargs):
    """Fail closed when an old Action snapshot requests a system executor."""
    raise PermissionError("Legacy system Action execution retired; use execute_typed with a canonical Attempt")
