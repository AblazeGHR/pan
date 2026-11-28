# Conditional Session queue wake

Within a single Pan service process, an authorized caller may read
`GET /api/sessions/{id}/queue/state-token`. The response includes a version,
adapter, terminal identity and status, history and queue revisions, pause and Illegal
flags, and pending queue item identifiers, sources, delivery states and lock
flags. It excludes message bodies. The caller decides whether that state is
acceptable; Pan does not infer an application-specific recovery policy.

`POST /api/sessions/{id}/queue/conditional-wake` takes `source`, a stable
`clientMessageId`, and `expectedVersion`. It inserts the configured Wake at
the queue head only if the whole projected version still matches at the
metadata replacement commit. A Session with unpersisted history is rejected until its history has committed. A `conflict` did not enqueue anything. `queued`
confirms queue admission, not consumption. `GET /api/sessions/{id}/queue/wake-receipt?clientMessageId=...`
queries the original durable queue receipt; `unknown` is not permission to
replay. A repeated request with an existing original Wake receipt returns that
receipt without changing its position. Existing ordinary Wake is unchanged.

The version covers only the returned fields. This contract uses the existing
per-process Session writer, queue lock and history summary lock; independent
Pan processes sharing the same Session data directory are not supported by
this contract. It is not a cross-process compare-and-swap.

Local script profiles need explicit new rules for each operation; old profiles
receive no additional permission. A bound plugin uses the same core primitive
through its existing Session scope. No account, quota, provider, or recovery
policy is encoded in this interface.
