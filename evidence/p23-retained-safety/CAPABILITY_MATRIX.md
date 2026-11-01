# P23 retained cleanup capability evidence

Uses existing `processOwner`; no new capability name or registry changes.

| Operation | Windows | Linux | macOS | Undeclared platform |
|---|---|---|---|---|
| Canonical independent explicit owned cleanup | Real HTTP + production runner + Job Object + FILETIME tested | Shared ProcessOwner interface used; not run here | Shared ProcessOwner interface used; not run here | `processOwner=false` explicitly rejected (unit + real HTTP 501) |
| Unknown cleanup inspect / explicit recheck | Real denied-probe injection only; native tree/kill/proof; cold interpreter recovery | Not run | Not run | No success fallback |
| Stale birth / reused PID rejection | Real live PID with deliberately wrong raw FILETIME, native verify/recover reject | Not run | Not run | Not inferred |
| Public canonical Run cancel -> independent cleanup | Missing shared connection, 2 red successors | Not run | Not run | Not advertised as working |
| Late terminal receipt after canonical retire | Real HTTP and actual independent terminal proof; original Job remains retired | Not run | Not run | Not inferred |

No OS-level PID reuse was forced. The test deliberately substitutes the wrong
birth against a real live PID to prove the production PID-reuse fence. Windows
ProcessOwner contract tests also run; their POSIX counterparts and Windows
constructor-on-POSIX gate are existing explicit skips on this Windows host.
No POSIX import/collection/CI pass is claimed. Platform backend code is unchanged.
