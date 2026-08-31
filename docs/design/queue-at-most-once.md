# Durable queue delivery semantics

`Session.queue_pending` is the durable inbox for both dashboard/user tasks and
agent/report/QQ messages.  The in-memory `pending_signal` queue contains only
wake-up signals; it is never the source of message content.

Each actionable item starts as `queued`.  The consumer claims it only after a
provider process is available, appends the receipt to history, and persists the
queue removal in one save.  That save is the **consumption boundary**: once it
succeeds, the item has been consumed exactly once from Pan's point of view.

The provider protocol does not expose an atomic “stdin accepted + terminal
result persisted” acknowledgement.  Therefore the queue item is removed before
the provider write.  If the provider or Worker dies after that save, Pan keeps
the history/task status but never puts the item back or automatically replays
it.  This deliberately prefers at-most-once execution over guaranteed
completion; a crash can leave a task incomplete, but it cannot make the same
queue item run twice through restart/watchdog recovery.

`in_flight` and `uncertain` are accepted only as one-time compatibility states
for data written by older versions.  On recovery those entries are finalized
and removed without replay; new items do not remain in `queue_pending` after
Worker receipt.  The retry endpoint remains as a compatibility route but always
returns an error, so no UI or API path can turn an already-consumed item into a
second execution.

Reports and QQ reminders are consumed as one batch at the same receipt
boundary.  Terminal results only update history/status and do not perform a
second queue acknowledgement.

Browser `clientMessageId` receipts and orchestration `taskId` values are also
checked against the durable queue/history, not only the process-local registry;
late reconnects and registry TTL expiry therefore cannot enqueue a second copy.
