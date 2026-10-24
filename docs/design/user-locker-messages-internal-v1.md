# Dedicated User Locker messages: internal admission and user reads

This extends the independent Locker panel with ordinary reading records. These
are **Locker msg**, not Session history, Interaction Cards, Agent Inbox queue
entries, or notifications. Nothing automatically forwards Chat, QQ or Job
content into this store.

## Admission boundary

`packages.interactions.locker_messages.accept_locker_message(source_session_id,
recipient_id, message)` is an internal Python adapter entry point. Its caller
must already hold the trusted source Session identity. The local stdio MCP `locker_message` producer uses this same boundary. A user
Bearer is not producer authorization.
The lower-level `InteractionStore.accept_message` is a storage primitive and
does not replace the source/attachment validation at the adapter boundary.

The source Session must exist at admission. Its identity and the recipient
determine the same `ulocker_` ID used by Cards. The first committed dedicated
message or Card creates that source's Locker; merely listing Sessions or
recording source metadata does not. Source name and effective Workspace IDs
are remembered so an existing Locker can still display after Session deletion.
Live sources continue to use current names and effective Workspace membership.

## Message document and persistence

The required Message-compatible fields are `messageId`, `role`, `content` and
an ISO `ts` with timezone. Optional `parts` use the existing text/attachment
shapes: text contains `text`; an attachment contains `attachmentId`,
`displayName`, and optional MIME/size/location metadata. Future fields and part
types are preserved as JSON rather than discarded or treated as execution
instructions. Parts are authoritative for presentation when they contain
known text/attachment parts; otherwise content is the fallback. The existing
public projection removes the provider-private `__serverPath` part field.

Identity is derived from `(recipient, source Session, messageId)`. Exact replay
is idempotent; changed content, timestamp, parts or future fields conflict.
Deletion retains only the derived identity, recipient and a signature digest;
reusing that identity cannot resurrect a deleted record. No notification is
created by ordinary message admission, reading or deletion.

Storage adds `locker_messages` and `locker_message_tombstones` to the existing
`cards.sqlite`, plus source snapshot columns in `user_lockers`. Migrations are
transactional and idempotent, do not rewrite old Card definitions/responses,
and retain the original Card API. SQLite uses the existing FULL synchronous
transactions. Ordinary retained records have a per-recipient capacity (the
store's default 1000); overflow rejects explicitly without trimming records.
The existing open-Card capacity rule is unchanged.

## Protected user endpoints

All endpoints below use the existing Automation UserAuth principal. Recipient
identity is never accepted from the user request. A different recipient sees
no records and cannot mark, delete or download another user's message.

| Method and path | Response / behavior |
| --- | --- |
| `GET /api/user-locker-messages?limit=100&after=...` | `{schemaVersion:1,messages:[...],nextAfter}`; limit 1..500 |
| `GET /api/user-locker-messages/{id}` | `{message: record}` |
| `PUT /api/user-locker-messages/{id}/read` | Exact body `{read:boolean}`; returns updated record |
| `DELETE /api/user-locker-messages/{id}` | Idempotent terminal reading-record cleanup; no notification |
| `GET /api/user-locker-messages/{id}/attachments/{attachmentId}` | Authorized bytes for an attachment actually referenced by that message |

A record contains `kind:'message'`, schema version, stable `id`, `lockerId`,
`ownerSessionId`, `recipientId`, `createdAt` (the original Message `ts`), the
original nested `message`, `read`, and `revision`. The revision fences delayed
read responses; it is not a consumer cursor or shared consumption state.
Query pagination uses the internal insertion sequence and a recipient-bound
opaque cursor. Paging never changes read state. A cursor remains usable after
the page's final record is deleted; concurrent new records can appear in later
pages. Cards retain their original query and response shapes.

`GET /api/user-lockers` counts both record types without a join multiplication.
Existing zero-count persisted Locker metadata remains API-compatible, while
the frontend omits empty sources. There is no aggregate row or message copy in
storage. The panel projects Workspace/source filters and sorts both record
types by parsed time and stable ID.

## Attachments and frontend identity

Opaque IDs are checked against the existing attachment registry at admission,
with no cross-source allowance. Supplied paths, hrefs, filenames, unknown
metadata and Markdown labels never grant access. The protected download route
rechecks message ownership, referenced attachment membership and the original
registry scope. It can read retained attachment bytes even after the source
Session is deleted; it does not recreate a Session. Existing Chat attachment
routes and their permissions are unchanged.

The panel reuses `MarkdownRenderer` and safe attachment Markdown. Locker alone
supplies an optional authenticated attachment-download callback; Chat's normal
file/editor behavior has no callback and is unchanged. Credentials stay in the
shared memory-only UserAuth store, never in download URLs or browser storage.
Logout and 401 hide and unmount all old-user rows/sources, and obsolete auth
responses cannot overwrite a successor login. Higher message revisions reject
late read-state results. Source/Workspace filters preserve drafts only while
the panel is mounted, as in the accepted Card stage.

Open Cards still allow only submit/reject. Terminal Cards and ordinary Locker
reading records can be deleted. Card trusted bindings, Queue unknown/rejection
semantics, original queue IDs, trigger receipts and the existing Job/Pause
lifecycle are unchanged. Submit and reasoned rejection notify only the source,
with response fields and `summaryBody[:200]`; reasonless rejection and deletion
do not notify.

## Local stdio producer and manual reading (2026-10-24)

`locker_message(operation="create", message={messageId, role, content, ts, ...})`
creates an ordinary record for this profile's `user`, with the source fixed to
`PAN_AGENT_SESSION_ID` resolved by the host. There is no source, owner or recipient
argument. The bound local stdio process uses the existing loopback caller bridge
(`X-Pan-Queue-Caller`); this is a same-user local process assertion, not a remote
credential. SSE/non-loopback targets, redirects, browser Origin/Fetch requests,
missing or unresolved callers are refused. This does not use UserAuth as a
producer grant or introduce a separate authorization system.

Discovery is the normal MCP tools/list and input schema; the existing schema
endpoint includes `$defs.LockerMessageProducer`. The host accepts only
`POST /api/locker-message-tools/create` with `{message: ...}` or `/get` with
`{messageId: ...}`. Unknown top-level fields (including source/recipient overrides)
are rejected; future fields inside Message remain preserved.

The original Message `messageId` is the stable request identity; persist it and
the exact document before calling. Same identity/document is idempotent, changed
content conflicts, and a deleted identity cannot be recreated. On an unknown
transport outcome call `locker_message(operation="get", message_id="original")`
to inspect the original record. Get never creates, queues, or resends; a not-found
result is not automatic retry authorization. Attachments use the same source-only
validation as internal admission. Results prove durable admission, not user reading.

Both source Locker and Workspace aggregation display chronological DOM order
(createdAt, then stable ID), initially at the latest bottom. Existing API cursors
are fully fetched under one abortable refresh generation; the display initially
reveals the latest 50 records. Scrolling to the top or clicking Load older messages
reveals 50 more and preserves the height-delta anchor. New records follow only
inside the 48px bottom zone. Reading older records does not force a tail jump.
This is display pagination over the existing bounded store, not a new server cursor.

Entering, viewing, scrolling, refreshing, switching sources/Workspaces and
aggregation never mark ordinary messages or Cards read. Only explicit row
read/unread and source Read all buttons change reading state. Card submit/reject
lifecycles keep their existing semantics, including their recorded read state.
