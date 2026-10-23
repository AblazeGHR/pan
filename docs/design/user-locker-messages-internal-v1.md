# Dedicated User Locker messages: internal admission and user reads

This extends the independent Locker panel with ordinary reading records. These
are **Locker msg**, not Session history, Interaction Cards, Agent Inbox queue
entries, or notifications. Nothing automatically forwards Chat, QQ or Job
content into this store.

## Admission boundary

`packages.interactions.locker_messages.accept_locker_message(source_session_id,
recipient_id, message)` is an internal Python adapter entry point. Its caller
must already hold the trusted source Session identity. There is no new public
producer HTTP CRUD or MCP tool. A user Bearer is not producer authorization.
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
