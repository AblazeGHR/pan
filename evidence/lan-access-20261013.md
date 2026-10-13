# LAN access verification, 2026-10-13

- Worktree: `D:/project/pan-worktrees/lan-access-20261013`
- Branch: `feature/lan-access-20261013`
- Baseline: `a1351d113a4a26e8ae1521d63ae4ce6e277f13c4`
- The checkout has no `.workflow` directory. Read `docs/plans&overviews/constraints-and-acceptance.md`; the task's stricter protected-port rules take precedence.
- Actual `main.py` uses `PAN_HOST`, default `127.0.0.1`. This legacy override changes the main binding, has no settings/lifecycle or LAN-specific management boundary, and is left intact. The new optional listener hosts the same ASGI app with lifespan off and proxy header trust off. Main/internal origin is unchanged.

## Results

- Related Python regression: **241 passed**, one existing Pydantic settings forward-reference warning. Suites: LAN, terminal REST/WS/tools, remote tunnel, remote MCP runtime, readonly, native control, native passthrough, self-queue and MCP port propagation.
- Final LAN-specific verification after handler/error/cleanup refinements: **20 passed**.
- Full frontend Vitest: **145 files, 1599 tests passed**. AppSettingsModal: **46 passed**.
- Production build and TypeScript: passed; **66 precompressed production assets** verified. Existing Vite mixed static/dynamic router import warning remains.
- Changed frontend source/test/E2E ESLint: passed. Full ESLint remains **27 errors, 18 warnings** in unchanged files (job-pin, manual-task-report, mobile-topbar-compact, settings-terminal-entry E2E and existing component warnings). No rules disabled.
- `compileall` for touched Python modules and `git diff --check`: passed.
- Chromium `node e2e/lan-settings.e2e.mjs`: passed on dynamically allocated loopback port; missing-status retry, invalid ports, save failure retaining form, startup failure without URL, active URL, stop, clipboard unavailable, all-interface wording, and 320/390/1280px no horizontal overflow. API responses are mocked in this UI test. No requests to 8767/8768.

## Real network and safety scope

- Real dedicated listener bound to this machine's non-loopback IPv4 address (`10.24.170.47` during verification) on dynamically allocated owned ports. No main application lifespan or real provider starts.
- Actual production `/api/health`, `/ws` ping/pong and `user_inject` durable queue acknowledgement exercised. Chat Session is temporary, paused, and guarded by an assertion preventing provider recovery; persisted message checked in pytest's temporary storage.
- Production terminal REST and `/ws/terminal/term_1` exercised through that real listener using the existing deterministic terminal service fake. No-Origin/no-Fetch-Metadata HTTP GET succeeds only on marked LAN entry; terminal WS hello succeeds for active exact LAN origin. Untrusted/missing WS origin rejected.
- Existing stdio MCP real ConPTY and terminal WS real ConPTY regression tests passed after installing sidecar dependencies into this checkout. This is separate from the LAN service-fake test and does not prove a full live provider/native session over LAN.
- Host/Origin mismatch, forwarded/caller header spoofing, private identity endpoints, `/ws/agent`, main control/global settings writes and readonly-management writes denied. All-interface entry tested with a loopback TCP client: still cannot borrow local-process identity.
- Occupied port, injected listener startup failure, socket rollback/rebind, save without changing active binding, explicit close/reopen, enabled/disabled startup controller behavior, malformed stored config preservation, and independent Tailscale origin union exercised.
- Test listener shutdown closes HTTP/WS connections and releases owned ports. No Tailscale CLI/Cloudflare Tunnel operations, firewall/network/proxy edits, production service changes, pushes or releases.

## Limits and integration

- No separate phone/device, Wi-Fi AP isolation, firewall traversal, live Tailscale Serve or live Cloudflare Tunnel coexistence was verified. Listener-running status is deliberately not a claim of external reachability.
- Startup restoration is controller-tested; a full deployed Pan process with production profile was not started or restarted. MA must load the new code in the normal deployment lifecycle; once loaded, LAN start/stop needs no main restart.
- Minimal usage is independently added in `docs/lan-access.md`. README, config example and shared workflow/overview documentation were not edited; any links or overlapping documentation from the independent documentation TA should be integrated by MA.

## Reproduction

```powershell
python scripts/run_tests_isolated.py -- D:\project\Pan-main\.venv\Scripts\python.exe -m pytest tests/test_lan_access.py -o addopts= -q -ra
# Related batch: additionally pass test_terminal_api.py, test_terminal_ws.py,
# test_terminal_tools_api.py, test_remote_tunnel_api.py, test_mcp_remote_runtime.py,
# test_readonly_session.py, test_native_control_api.py, test_codex_native_passthrough.py,
# test_self_queue.py and test_mcp_port_propagation.py, all under tests/.
cd packages/web
pnpm exec vitest run
pnpm build
node e2e/lan-settings.e2e.mjs
pnpm exec eslint e2e/lan-settings.e2e.mjs src/components/layout/LanSettings.tsx src/components/layout/AppSettingsModal.tsx src/components/layout/AppSettingsModal.test.tsx
```
