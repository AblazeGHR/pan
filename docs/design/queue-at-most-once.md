# Durable queue delivery semantics

`Session.queue_pending` is the durable inbox for both dashboard/user tasks and
agent/report/QQ messages.  The in-memory `pending_signal` queue contains only
wake-up signals; it is never the source of message content.

Each actionable item has a `deliveryState`:

- `queued`: not handed to a provider.  A worker spawn/restart or watchdog may
  dispatch it automatically.
- `in_flight`: Pan claimed it and persisted that claim before writing stream
  stdin or spawning a one-shot process.  If the worker then disappears before
  a terminal result, execution is uncertain and Pan does **not** replay it.

The provider protocol does not expose an atomic “stdin accepted + terminal
result persisted” acknowledgement.  Persisting `in_flight` before hand-off is
therefore the only safe automatic boundary for at-most-once execution.  The
trade-off is that a crash immediately after the state save can leave a message
waiting for operator action rather than silently risking a duplicate.

The queue API exposes `dispatchState=uncertain` when an `in_flight` item no
longer belongs to a live worker.  `POST /api/sessions/{session_id}/queue/{item_id}/retry`
explicitly resets it to `queued`; the UI labels this action as potentially
duplicating a provider-side execution.  Reports and QQ reminders are consumed
as one batch, so retrying one member retries the persisted report batch.

An item that is known not to have reached a provider (for example, a dead
stream process detected before write) is returned to `queued` automatically.
After a terminal result confirms an item, the next queued task/report signal is
released so a restart cannot strand later messages behind an acknowledged item.

