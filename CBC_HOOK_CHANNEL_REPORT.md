# CBC hook channel and DIRECT DNS delivery

Task: T-CBC-HOOK-CHANNEL-20261014/1. Worktree: `D:/project/pan-worktrees/cbc-hook-channel-20261014`, branch `fix/cbc-hook-channel-20261014`, baseline `097a356efa840247a7802141636fa0f1f53bfc9f`.

## Result and acceptance boundary

The user-authorized proxy repair is applied: every Mihomo DIRECT exit uses system DNS. The existing global `+.edu.cn: system` override is preserved. The active proxy was hot-loaded without force; HTTP 204 was returned and Mihomo PID 26688 remained unchanged. Three anonymous HTTPS probes through the active 7890 proxy succeeded, with TLS setup of 102.2, 95.7 and 79.3 ms. Each probe matched `DomainSuffix/tencent.com -> DIRECT`; concurrent outbound sockets included system-DNS address `43.242.199.79`. HTTP 404 from anonymous HEAD `/` establishes TLS/HTTP reachability, not a model/provider turn.

The original TUI's generic `CBC control channel unavailable` does not identify its exception. Its exact incident cause remains unconfirmed. The verified provider DNS failure and the local hook channel must not be represented as the same proven failure. Source repairs make the hook fail closed on malformed input or absent environment and expose fixed, sanitized HTTP rejection codes.

## Network evidence and repair

Observed existing CBC PID 35740 connected to 127.0.0.1:7890. Controller connection source port 58288 matched that socket and showed `copilot.tencent.com`, rule `DomainSuffix/tencent.com`, chain `DIRECT`. Current TUN was disabled. Thus the DIRECT rule was working, although CBC traffic still traversed the local HTTP proxy.

System resolvers 172.18.1.92 and 172.18.1.93 returned `43.242.199.79`; active Mihomo DNS returned `43.141.70.102` and `43.141.132.124`. Same-day logs repeatedly recorded DIRECT dial timeouts to both latter addresses, including 22:57, 22:58, 23:04 and 23:19. Independent direct socket probes found `43.242.199.79` successful twice, `43.141.132.124` TCP-timeout twice, and `43.141.70.102` reachable but variable in setup latency.

An initial inspection read the GUI base configuration. The generated effective `work/config.yaml` actually used subscription-provided DNS upstreams, overriding the base DoH settings. Therefore stale cache alone is not established: resolver choice/CDN address selection can explain the differing results. The isolated fresh base-DoH experiment is retained as a separate observation, not proof that the active resolver cache was stale.

An owned deterministic DNS fixture deliberately returned TEST-NET `203.0.113.1`. With default DNS, both anonymous HTTPS connections failed. With `direct-nameserver: [system]`, the same initial DNS answer remained visible but both DIRECT HTTPS connections succeeded (204.9/107.0 ms). This proves DIRECT re-resolution, independent of the resolver's initial answer.

After the user's explicit “采用这个方案，所有direct都用系统dns”, only these fields were inserted into the existing global override and generated running configuration:

```yaml
dns:
  direct-nameserver:
    - system
  direct-nameserver-follow-policy: false
```

Files: `C:/Users/14709/AppData/Roaming/mihomo-party/override/edu-cn-direct-dns-20261010.yaml` and `.../work/config.yaml`. Semantic comparison verified all other configuration remained identical. The override remains global and linked to the active subscription. The original DNS-only override and before/after file hashes are retained in the evidence folder. No subscription, node selection, system DNS, system proxy setting, CBC configuration or credential was changed. DNS cache flush was not performed.

This setting chooses the system resolver at DIRECT egress. Initial DNS queries used for routing can still go through Mihomo; IP-literal destinations have no domain to re-resolve. Future network changes may change system DNS results. GUI regeneration/subscription switching and a real CBC model turn were not exercised.

References: [Mihomo DNS settings](https://wiki.metacubex.one/en/config/dns/), [resolution flow](https://wiki.metacubex.one/en/config/dns/diagram/), [controller API](https://wiki.metacubex.one/en/api/).

## Hook trace and source repair

`CbcNativeAdapter` launches one resident bridge. The bridge creates a random per-runtime capability and ephemeral 127.0.0.1 listener, merges adapter settings into a temporary settings file, appends the command hooks, and supplies URL/token in the ConPTY child environment. Neither is included in command arguments. The installed CBC 2.164.0 command HookExecutor serializes internal hook fields while removing the session object; generation context and FinalStop reason are available on that command path. Its command-hook exit handling treats exit 2 as blocking and other nonzero codes as nonblocking.

The old hook parsed input and accessed required environment variables before its exception handler. Missing environment or malformed JSON could therefore produce an uncaught exception/exit 1, which CBC interprets as nonblocking. Baseline red tests reproduced KeyError/JSONDecodeError in three exact nodes. Parsing and request setup are now guarded; malformed input returns 2 and absent capability environment returns 2 for UserPromptSubmit. Other lifecycle hooks retain their existing nonblocking exit policy.

The old HTTP handler returned generic 409 HTML; the client collapsed 409, 403, connection failure, timeout and response parsing into one message. The handler now returns only fixed protocol codes for wrong Session, stale turn, orphan continuation, active turn, prompt mismatch and missing owner. The client distinguishes these, authentication failure, loopback connection failure and timeout. It never renders raw exception strings, URL, event values, arbitrary server bodies or capabilities. Request timeout remains 10 seconds and CLI hook timeout remains 12 seconds.

ProxyHandler({}) already bypassed inherited/Windows proxy routing for the numeric loopback hook endpoint; it remains in place. That request performs no `copilot.tencent.com` DNS lookup. The provider DNS issue is consequently not a demonstrated direct cause of the UserPromptSubmit HTTP failure. A new TUI diagnostic or incident-correlated listener/HTTP rejection would be required to close that exact incident.

No owner/generation admission or PTY write behavior was weakened. Partial-write uncertainty, redispatch/takeover fences, same-kernel switching, queue gates and FinalStop-only completion retain their existing tests. No hooks were removed or failure allowed through. Startup/restart fixtures verify temporary settings cleanup, fresh capabilities across runtimes, and retired endpoints refusing submission. Startup ordering was inspected but not changed without a failing reproduction.

Native slash commands can modify effective CLI settings or Session identity. Installed CLI-settings provider source shows an in-memory settings overlay; it does not prove every slash path avoids persistent user settings. No user profile/slash command was exercised. Stale persisted hooks, changed Session identity and native settings persistence remain explicit risks rather than asserted incident causes.

## Validation and evidence

Evidence directory: [artifacts/cbc-hook-channel-20261014](artifacts/cbc-hook-channel-20261014).

- `red.xml` / `red.log`: baseline hook loaded in memory, 3 expected failures. Red-test execution used a cleaned environment and short tracebacks. The first red invocation's verbose pytest traceback expanded inherited environment values; those artifacts were overwritten with sanitized rerun evidence and are not part of delivery.
- `regression.xml` / `regression.log`: 213 passed, 0 failed, 0 skipped. CBC bridge/arguments/models/import/restart, Codex native transport/passthrough/completion, queue latency/restart/API/self-queue suites. Existing dependency deprecation warnings remain; assertions/timeouts were not relaxed.
- `conpty-hook.xml` / `conpty-hook.log`: 1 passed, 0 failed, 0 skipped. Real Windows owned ConPTY spawn -> generated CLI settings -> hook command subprocess -> authenticated loopback HTTP. SessionStart succeeds; unowned UserPromptSubmit is rejected with missing_owner; the owned root and Job are retired. This added OS probe is Windows-specific.
- `direct-system-fixture.json`: owned DNS and Mihomo profiles, default red versus system-DIRECT green; all owned processes retired.
- `proxy-observation.json`, `dns-endpoint-probes.log`: redacted live rules, DNS and network failure evidence.
- `proxy-config-change.json`, `global-override-before.yaml`: authorized minimal configuration change and rollback fragment.
- `active-proxy-after.json`, `active-proxy-forwarding.json`: post-change active proxy TLS/HTTP and matching DIRECT logs.

Total Python validation: 214 passed across the regression batch and additional OS probe. No real CBC model/provider turn, GUI/browser acceptance or frontend build was run. There are no frontend changes, so build/UI verification is not required for this source patch. Existing CBC processes, 8767/8768, main/practical, push and Pan restart were not operated. MA must independently accept source and coverage before authorized main -> practical FF/build. The exact original hook incident remains an acceptance limitation.

## Follow-up: verified prompt-receipt mismatch repair, 2026-10-15

The user supplied the full screenshot. It still shows the generic old diagnostic and the practical hook path. Practical remains at `097a356e`; the first diagnostic commit has not been deployed. No new HTTP rejection reason can be inferred from this screenshot.

Installed CBC 2.164.0 `executeUserPromptSubmitHooks` builds its command-hook prompt as `ed.join("\n").trim()`. Pan stored the original PTY input and compared it verbatim with that projected prompt. Consequently `"  managed  "`, leading/trailing CRLF, BOM/NBSP and line/paragraph separators all cause a legitimate managed submission to raise `CBC prompt receipt does not match its input`, resulting in HTTP 409 and the old generic blocked message. This is a verified trigger of that error class; it is not proof of the screenshot's individual trigger.

`tools/probe_cbc_prompt_hook.mjs` executes only this actual installed method with deterministic managers. It does not bootstrap the CLI or invoke a model. The captured bundle SHA256 is `2d8e7c1aab61d62f983c85d9fc473c85a2d22afb0e13683920586ba69e82b4a3`. Four captured real-method hook facts all contain `"managed"`, while the inputs retain their whitespace. `installed-prompt-method.json` preserves that evidence. Generation context is explicitly supplied by the test harness; this probe does not claim to exercise a real CLI generation lifecycle.

Four targeted tests failed before the source repair (`prompt-red.xml` / `prompt-red.log`). The repair compares the receipt against the pending input's exact JavaScript trim projection. Original PTY bytes and pending intent remain intact. It does not trim the received event or normalize internal whitespace, line breaks or substantive content. The explicit ECMAScript whitespace set excludes Python-only strip characters such as U+0085. Pure ECMAScript-whitespace input is rejected before writing, because CBC emits no UserPromptSubmit for an empty projected prompt.

Additional tests preserve original PTY bytes, reject changed content, retain pending intent on mismatch, reject empty projected prompts before input, and drive all captured installed-method facts through the actual hook subprocess and authenticated production loopback HTTP handler. Stop alone still does not finish a turn; FinalStop settles each fixture turn once. Existing owner, stale generation, partial-write uncertainty, native switching and queue tests remain unchanged.

The complete follow-up regression batch is **227 passed, 0 failed, 0 skipped**, recorded in `prompt-regression.xml` / `prompt-regression.log`. It includes the earlier CBC/Codex/queue suites and the real owned Windows ConPTY hook/environment probe. Only existing dependency warnings remain. No assertion or timeout was relaxed.

### Real installed TUI attempt and limit

At the user's request, `tools/probe_cbc_tui_no_model.py` starts the actual installed CBC through the production native bridge/ConPTY transport. It supplies a clean environment, dedicated temporary HOME/USERPROFILE/CODEBUDDY_CONFIG_DIR, isolated workdir and no inherited provider credentials or MCP configuration. It disables updates/telemetry and adds an unconditional UserPromptSubmit fixture hook that records execution and exits 2 before any provider turn. It attempts submission only after SessionStart.

The actual TUI launches, emits SessionStart and accepts PTY input, but the private profile reaches the login UI. The submit guard execution witness is absent. Therefore this attempt establishes real TUI startup and input transport, **not successful real TUI UserPromptSubmit acceptance**. `real-tui-no-model.json` records the distinction. The owned bridge and its child processes are retired and temporary profile/settings are removed. No existing CBC process, user CBC profile, credential, protected service or real model/provider turn is operated.

MA acceptance should distinguish: (1) the now-proven trim mismatch trigger and its protocol/HTTP repair, (2) earlier diagnostic/closed-failure changes, (3) actual TUI startup proof, and (4) the original screenshot incident and authenticated real submission, which remain unverified. Accepting and deploying these source repairs does not itself establish that the screenshot incident is closed. A later authenticated real submission requires explicit restoration of CBC test authorization and confirmed safe runtime retirement/recreation; the TA does not perform it or move main/practical.
