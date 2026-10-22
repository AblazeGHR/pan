"""Transport-neutral declarations for future adapters and UI clients.

query is a bounded predicate tree; parallel remains a reserved extension value.
"""
from typing import Any, Literal, NotRequired, TypedDict


class RuntimeSource(TypedDict):
    type: Literal['core', 'plugin']
    id: str


class EventDefinition(TypedDict):
    type: str
    source: RuntimeSource
    schemaVersion: int
    schema: dict[str, Any]
    available: bool


class Event(TypedDict):
    id: str
    type: str
    source: RuntimeSource
    schemaVersion: int
    occurredAt: str
    payload: Any


class SessionSelector(TypedDict):
    mode: Literal['explicit', 'event', 'query']
    ids: NotRequired[list[str]]
    path: NotRequired[str]
    query: NotRequired[dict[str, Any]]


ExecutionMode = Literal['serial', 'parallel']
RunStatus = Literal['queued', 'running', 'succeeded', 'failed', 'cancelled', 'uncertain', 'skipped']


class EventReceipt(TypedDict):
    event: Event
    accepted: Literal[True]
    persisted: Literal[True]
    duplicate: bool
    rejected: list[str]  # Live subscriber delivery is not acknowledged here.


class RunAttempt(TypedDict):
    id: str
    number: int
    status: RunStatus
    startedAt: NotRequired[str]
    finishedAt: NotRequired[str]
    legacy: NotRequired[bool]
    error: NotRequired[str]
    result: NotRequired[Any]
    targetIds: NotRequired[list[str]]
    targetResults: NotRequired[list[dict[str, Any]]]
    selection: NotRequired[dict[str, Any]]
