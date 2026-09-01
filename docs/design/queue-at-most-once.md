# Durable queue hand-off semantics

`Session.queue_pending` is the only durable delivery queue for dashboard/user
tasks, agent tasks, reports and QQ reminders. `Worker.pending_signal` is an
in-memory wake-up channel only: it contains no message body and is discarded
when a Worker generation ends.

## Delivery unit and FIFO

The consumer reads the durable FIFO head. A task is one delivery unit. A
contiguous run of report/QQ items is one report delivery unit; it is formatted
as one prompt and is delivered all-out or all-back. The consumer never skips a
head item to execute a later task, and there is only one active consumer per
Worker generation.

New rows use a typed envelope with a server-generated `id`:

```json
{
  "type": "task",
  "id": "<uuid>",
  "text": "请检查这个项目",
  "source": "user",
  "taskId": null,
  "clientMessageId": "browser-uuid",
  "deliveryState": "queued"
}
```

`type` and `source` are separate. Legacy rows without a type are normalized
in place: text rows become user tasks, result rows become reports, and QQ
rows receive an id/source. The migration is idempotent and runs before
recovery; the offline command is
`python scripts/migrate_queue_delivery.py --apply`.

## Handoff state machine

```text
queued
  └─ reserve (persisted) → reserved
       └─ provider attempt → writing
            ├─ stream: full bytes write + stdin.drain succeeds
            │     └─ sent_to_cli → remove queue row
            ├─ one-shot: process creation succeeds with full adapter argv
            │     └─ sent_to_cli → remove queue row
            └─ failure/cancel/crash before confirmation → queued + backoff
```

`writing` may be an in-memory refinement of the persisted reservation. The
durable reservation is enough for restart recovery. `sent_to_cli` is a durable
marker used for the crash window between successful handoff and queue-row
removal; recovery removes such rows without waiting for a provider result.

The queue-removal boundary is provider handoff, not business completion:

- Stream adapters call the queue commit callback only after the complete
  serialized bytes have been written to CLI stdin and `write + drain` has
  succeeded.
- One-shot adapters call it immediately after successfully creating the CLI
  process with the complete request in the existing adapter argv protocol.
- Terminal result, history enrichment, provider exit and frontend rendering do
  not decide whether the item is removed.

If Pan cannot determine whether the provider accepted a write, the item is
requeued with bounded backoff. This intentionally accepts a narrow duplicate
window: the CLI may have received the request before Pan persisted the
requeue. It is preferable to silently losing a durable pending item.

## API and UI rules

The normal frontend send path always calls the durable server send endpoint,
including while the Worker is running. The server persists the item and then
returns the queued acknowledgement; it never interrupts the current turn or
writes directly to stdin. The queue panel mirrors `GET /api/sessions/{id}/queue`
and hides reserved/writing/sent rows, so a new queued item remains visible while
the current item is being handed off or processed.

Delete and retry operate on the original `queueItemId`. Retry clears the
original row's backoff and wakes the same receipt; it never creates a copy.
`clientMessageId` and orchestration `taskId` are checked against the durable
queue/history as well as the bounded in-memory cache.

## Recovery and compatibility

On Worker restart, `reserved`, `writing`, old `in_flight`, `write_failed` and
`unknown_after_crash` rows are restored to `queued` with persisted retry
backoff. `sent_to_cli` rows are removed. Original session files are preserved
by the migration script in a timestamped backup directory. Running the script
again is a no-op for already migrated files.
