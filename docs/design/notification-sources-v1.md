# Locker / error notification sources v1

## User settings

- **App Settings → Behavior → Session errors → msgBridge** controls whether a new
  Session error uses that Session's System, Browser and QQ Report connections.
  It defaults to enabled to retain the existing QQ error-report behavior. Turning
  it off does not disable the independent global error source.
- **App Settings → Notifications → Locker new unread / New errors** provides two
  independent sources, both disabled by default. Each has Enabled, System,
  Browser, QQ Report and QQ contact selection, with an explicit Save button.
  QQ contacts reuse bot-qualified identities and the existing contact search.
  These sources have no receiving Session, Subscribe or inbox configuration.
- An error's Session and global routes form a union. The same System path,
  Browser path or exact QQ contact/bot identity creates one durable attempt,
  including when both settings enable it. Different targets are separate
  attempts. A legacy unqualified QQ target and an explicitly bot-qualified target
  remain different configured routes, as in the existing QQ Report contract.
- Session completion bells, QQ Subscribe/Report, Browser permission controls and
  the existing Locker desktop/count-poll notification keep their interfaces.
  Successful completion notifications still use their existing implementation.
  Error forwarding now uses the shared event ledger instead of also creating a
  second terminal QQ Report outbox record.

## Authoritative sources and privacy

Locker admission is transactional with creation of an unread interaction card,
creation of a dedicated Locker message, or the first answer to a user-created
card. Retrying creates, polling counts, reading/unreading, refresh, reconnection
and restart do not create a new notification identity. User-card creation itself
is read and does not notify. Source content is admitted only for local recipient
`user` with an existing, non-readonly source Session; the same source check runs
again before claim and authenticated Browser content reads.

The global error source covers all Sessions' existing classified events at
`session_error_log.record_session_error[_async]`: `terminal_error`, `worker_crash`,
`worker_start_failure`, `native_operation_error`, `transient_provider_error`, and
`tool_failure`. A structured product error without a Session can use the global
route; a nonempty but unavailable Session still fails closed. Cancelled events are excluded. Their stable `se_` event ID is the
deduplication key; two different authoritative error event IDs remain different
events. This is not a subscription to arbitrary logging calls, historical error
text, HTTP validation failures, or the independent Job service's lifecycle.

The body contains source Session, item/event ID, task correlation when available,
and a bounded message/card summary or error text. Existing credential redaction
is applied, QQ CQ markup is neutralized, and the total is limited to 1200
characters. Raw provider payloads, diagnostic scratch files, history snapshots,
attachment bytes and attachment metadata are not forwarded.

## Persistence and delivery

Settings persist in `config.json`: `ui.sessionErrorBridge` plus its server-managed
epoch and `ui.notifications.lockerUnread/newError`, each containing `enabled`,
`system`, `browser`, `qqReport`, `qqTargets` and a server-managed epoch. API types
are strict; QQ Subscribe and unknown source fields are rejected. Saving a source
or Behavior preference starts a new epoch. An old event is never admitted to a
new epoch or a newly selected target. Pending grants from changed Session
connections are retired without removing an independent global grant.

The existing Locker SQLite database gains `source_notification_events` and
`source_notification_attempts`. Event uniqueness is `(source, identity)`;
attempt uniqueness is `(source, identity, target)`. The event retains grants
from the enabled origins at admission. Disabled/invisible events retain their
identity with empty content/targets so re-observation cannot backfill history.
Locker intent failure rolls back its savepoint and leaves content admission
intact. Error notification failure cannot prevent the original error sample.
Async error admission runs in the existing FIFO error writer, preserving sample
sequence ordering and keeping SQLite off the event loop.

One server-owned drain runs while Pan is up, checks current grants and source
visibility, and commits `attempted` before invoking the existing QQ sender or
System sender. Closing the webpage does not stop QQ/System forwarding. A setting
turned off cancels unclaimed grants; an already claimed/in-flight external send
cannot be recalled. `queued`, `unknown`, `failed` and `attempted` are distinct
from `sent`; no attempted/unknown/queued effect is automatically resent, even
after restart. A crash after claim can lose a notification, preserving at-most-one
attempt rather than risking duplicate sends. Provider `sent` is a provider
receipt, not proof of human consumption or OS display.

Browser forwarding uses a live `notification.source` wakeup with only a hashed
reference. `GET /api/notifications/browser-events/{ref}` requires existing
Automation UserAuth and rechecks source/grants before returning content.
The open page constructs the notification only when permission is already
granted; it never requests permission from a background event. There is no
Browser history replay. The existing System sender reports unsupported platforms
explicitly; this feature adds no platform-specific implementation.

## Shared future-error exclusions

Behavior → Session errors → msgBridge and Notifications → New errors embed the
same `ErrorNotificationExclusions` component. One persisted object,
`ui.errorNotificationExclusions = {enabled: boolean, types: string[]}`, controls
both routes. Disabling retains selections. Each checkbox saves through the
existing UI settings endpoint with Automation UserAuth; failed writes leave the
committed store unchanged. A settings broadcast refreshes other open clients.
The list has client-side search and a fixed 160px scroll window. Locker unread,
completion notifications, error samples/history and manager reports are unaffected.

Authenticated `GET /api/notifications/error-types` supplies the only selectable
catalog. Its exact identities combine the structured `cli.errorSource` with
`cli.errorCode` or `cli.error.code/codexErrorInfo`. The catalog reuses existing
runtime structured code vocabulary, including `codex_error_payload/usageLimitExceeded`
and `codex_error_payload/serverOverloaded`; it does not classify message text or
scan logs. Missing/unknown sources or codes forward by default. Arbitrary client
types and malformed values are rejected; missing/malformed legacy objects read
as disabled with no selections. This is a bounded known vocabulary, not a claim
to enumerate every possible provider error.

Changing the canonical object updates server-managed
`ui.errorNotificationExclusionsEpoch`; identical saves do not rotate it. The
existing event ledger adds `exclusion_epoch` (empty for legacy events). Only new
admission applies exclusions, before either Session or global grants are built.
Excluded events keep their identity with empty text/targets to prevent replay.
Previously admitted events keep their original grants and exclusion epoch;
changing exclusions cannot revoke, reroute or replay them. Existing channel,
target and visibility checks still apply when claiming a send.

## Exclusion validation

See `artifacts/notification-error-exclusions/REPORT.md` for this extension's
commands and evidence. Original source-notification evidence below is retained.

Use `tests/test_source_notifications.py`, `tests/test_notification_http.py` and
`packages/web/src/components/layout/SourceNotificationPanel.test.tsx`. The HTTP
fixture owns its temporary profile and random port, uses production HTTP,
UserAuth, SQLite, built React and Chromium, records QQ/System instead of calling
providers, and rejects CLI Worker spawn. Browser notification construction is
also recorded explicitly. See `artifacts/notification-locker-error/REPORT.md`
for commands, counts, retained failures and current unverified layers.
