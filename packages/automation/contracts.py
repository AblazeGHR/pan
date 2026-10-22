"""Transport-neutral declarations for future adapters and UI clients.

query is a bounded predicate tree; retry/parallel are bounded backend policies.
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
RunStatus = Literal['queued', 'retry_wait', 'running', 'succeeded', 'failed', 'cancelled', 'uncertain', 'skipped']


class RetryPolicy(TypedDict, total=False):
    enabled: bool
    maxAttempts: int  # 1..10, includes initial attempt; default 3.


class TriggerScheduling(TypedDict, total=False):
    executionMode: ExecutionMode
    maxConcurrency: int  # serial=1; parallel defaults 3, range 1..32.
    retry: RetryPolicy


class ConfirmRecovery(TypedDict):
    expectedRevision: int
    confirm: Literal[True]


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
    reason: NotRequired[Literal['automatic', 'user-confirmed']]
    error: NotRequired[str]
    result: NotRequired[Any]
    targetIds: NotRequired[list[str]]
    targetResults: NotRequired[list[dict[str, Any]]]
    selection: NotRequired[dict[str, Any]]
