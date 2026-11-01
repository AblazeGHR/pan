"""Canonical final-runtime import path for the WP05 typed executor."""

import asyncio
import copy

from packages.jobs.independent_executor import IndependentExecutor as _IndependentExecutor
from packages.jobs.independent_executor import ExecutionConflict, effect_id, fence


class IndependentExecutor(_IndependentExecutor):
    """Canonical cancellation adapter for the original WP05b resource."""

    async def cancel_effect(self, *, attempt, effect_key):
        identity = effect_id(effect_key)
        doc = await asyncio.to_thread(self.client.get_execution, identity)
        if (doc.get("effectId") != identity or doc.get("jobId") != self.job_id
                or doc.get("effectKey") != dict(effect_key) or fence(doc.get("attempt", {})) != fence(attempt)):
            raise ExecutionConflict("cancellation resource identity/fence mismatch")
        result = await asyncio.to_thread(self.client.cancel_execution, identity, copy.deepcopy(dict(attempt)))
        if (result.get("effectId") != identity or result.get("effectKey") != dict(effect_key)
                or result.get("jobId") != self.job_id or fence(result.get("attempt", {})) != fence(attempt)):
            raise ExecutionConflict("cancellation receipt identity/fence mismatch")
        receipt = result.get("result")
        if not isinstance(receipt, dict) or receipt.get("effectKey") != dict(effect_key):
            raise ExecutionConflict("cancellation effect receipt missing")
        return receipt

from packages.actions.typed_executor import (
    ChannelProvider,
    EffectExecutor,
    EffectReceipt,
    InMemoryReceiptStore,
    ReceiptStore,
    SessionProvider,
    SystemExecutor,
    TypedExecutor,
    WorkerSessionProvider,
)

__all__ = [
    "ChannelProvider",
    "EffectExecutor",
    "EffectReceipt",
    "InMemoryReceiptStore",
    "IndependentExecutor",
    "ReceiptStore",
    "SessionProvider",
    "SystemExecutor",
    "TypedExecutor",
    "WorkerSessionProvider",
]
