# Remaining canonical and lifecycle cohort

Original Job: job_42b3858fe51b7763652fbf00, source b9d18e6404ad0d9bf8ee2913bbed329f447ceb65.
94 canonical nodes: 77 passed, 16 failed, one browser teardown error. Source drift was empty.
The old isolated-host safe-api node failed; the same unchanged business assertions
passed using the existing genuine-main owned bootstrap. Original JUnits retained.

## QQ root cause and successor

Real wire diagnostics preserved privately show plugin HTTP admission succeeded,
but NoneBot's HTTP client constructed three verified TLS contexts, about 1.7s
each, before sending the owned OneBot request. The parent original 5s timeout
expired first. Client proxy discovery inherited workstation registry defaults.
NO_PROXY=* confines this all-loopback fixture to its owned direct endpoints and
eliminates unrelated proxy transports; no timeout or TLS verification changed.
The first no-proxy probe then exposed real Event loop is closed teardown errors:
repeated asyncio.run had closed the loop owning the cached HTTP connection.
The fixture now owns one asyncio.Runner for send/retry/cold lookup/client close.
Product sender, OneBotChannel.send, Bot.call_api and actual HTTP responses remain
real. No product transport fallback, exception swallowing or skip was added.

qq-loop-owned-green.xml: all eight real HTTP/observation nodes passed, including
both bot routes, exact frozen Unicode/CQ payload, actual lost ACK and missing-id
unknown, original identity cold lookup, no repeat send, and proven no-send retry.
qq-offline-current-proof.xml: all 22 current-product parity nodes passed. Three
old 409 expectations predated existing qqNoSend proof. Their successors verify
reconnectable_no_send against original Attempt/effect/arguments, explicitly retry
with the same Run/effect and new Attempt, exact rejection again while disconnected,
then multiple pulses without additional attempts/events. Raw error codes alone
remain insufficient, covered by the existing adversarial proof contracts.

## Quota and lifecycle

quota-current-three.xml: three passes. The fixture now explicitly reads an empty
owned quota profile and unavailable owned CLI; enabling the host cannot discover
workstation accounts. Existing NativeQuota accepts a once query via Creator/Trigger/
Job composition; monitor still rejects an unknown current account. Tests verify
exact accepted configuration, idempotent original admission, empty execution before
dispatch, no commands/effects, CAS and actual listener reopen. The old manual-writer
fence is not used as the expected canonical once response.
The original real HTTP node reuses the existing full two-process native unknown
successor, adding the original auth-session/logout refusal checks. This path still
requires a new formal run: original unknown Event/Run/Attempt/effect, no retry,
no replay after process restart, and auth-before-410 retired routes are mandatory.

lifecycle-default-genuine-green.xml: original safe-api node passed through the
byte-for-byte copied real main.py in its private profile. Normal collection reuses
the existing instrumentation fixture; its identity and original queue/legal-state
assertions remain. Full seven lifecycle cases remain to be run together.
Browser teardown error remains unresolved; diagnostic-only pending task stack
capture was added before its original not-thread.is_alive assertion. No timeout
increase or force shutdown was added.

Current focused greens are intermediate evidence. The complete failure map and
final candidate SHA CI remain outstanding. main/practical and real services were
not changed.
