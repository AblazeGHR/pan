# Sustained natural output and slow observer isolation

Measured in the isolated implementation worktree, Windows ConPTY + production
launcher/service/REST/WS + headless emulator, on an owned ephemeral loopback
server. No existing Pan service, provider, account, or port 8768 was used.

## Accepted measured subset

`tests/test_terminal_steady_pressure_ma.py` uses a real three-minute producer and
two real WebSocket clients. One consumes and acknowledges normally; the other
stops receiving after hello and only drains its backlog after production ends.
There is no injected sender delay or reduced product queue limit. Transport ping
timers are disabled in this fixture to isolate server backpressure from the
independent ping/pong timeout of an unread client. Production defaults are unchanged.

The uv run `steady-pressure-marker-control-uv.xml` passes, 185.67s:

| Measurement | Actual result |
| --- | --- |
| Producer stdout payload / duration | 111,520,800 bytes / 180.094s |
| Fast connection observed PTY bytes | 120,470,765; zero gaps |
| Slow peer's actual close frame | 1013 |
| Slow receive backlog subsequently drained | 20,154,048 JSON-text bytes |
| Application queue observed peak | 1,781,628 bytes; 59 queued items |
| Application queue bound | 4 MiB / 256 items, including in-flight frame |
| Raw retained log at end | 253,779 bytes, below 262,144 |
| One-second samples / owner beats | 180 / 1→179 |
| Owned launcher RSS first / last / observed peak | 27,660,288 / 29,470,720 / 29,687,808 bytes |
| Identity and cleanup | Original PID/FILETIME preserved; explicit close = exited |

PTY bytes are not producer stdout byte conservation: ConPTY adds terminal control
sequences/wrapping. Application queue accounting is not a bound on TCP buffers,
WebSocket library buffers, or total process RSS. RSS is a finite observation,
not a no-leak or hours-long steady-state guarantee. The slow connection is
isolated; it does not stop the foreground producer, fast peer, or owner heartbeat.

## Actual product repair

`asyncio.wait(FIRST_COMPLETED)` does not propagate a completed child task's
exception. Previously an admission overflow in reader/receiver, unlike a sender
timeout, could cause the route to return without a close frame. Three deterministic
gates (item overflow, byte overflow, arbitrary reader failure) all fail before the
repair (`ws-queue-close-pre.xml`). The route now consumes completed task failures
and sends 1013, with no exception text. Normal peer disconnect and explicit
terminal-state handling remain separate. Post-fix WS suite: 33/33 direct; WS plus
MA gates: 43/43 uv. Their JUnit files are retained.

## Failed fixture history, not rewritten

`steady-pressure-initial-uv.xml` passes the sustained output/heartbeat/identity/
queue assertions but fails its final close-code observation (no received close
frame). It does not identify the exact cause; default transport ping timers were
also active. Do not retroactively attribute that run solely to the product repair.

`steady-pressure-ping-isolated-uv.xml` times out waiting for the fast final marker,
although the owned producer finished 180s and wrote its completion file. The
fixture previously searched only the final 128 bytes of each read, which can
miss a marker in the middle of a large block. The final fixture searches the
entire combined block, retains a tail only for cross-block matching, and can
witness a marker in an explicit gap-recovery snapshot. The original timeout
is retained; its exact lost-marker location was not captured.

Sampler failure cannot bypass host cleanup: nested finally always requests
official lifespan shutdown, then retries owned service cleanup when necessary.
All close assertions stay exact; the successful run is not made by accepting an
unknown close code or loosening the product queue bound.

Cross-platform, remote clients, actual provider TUI stress, hours-long memory
trends, cross-user security, and kernel-wide resource bounds remain unmeasured.
