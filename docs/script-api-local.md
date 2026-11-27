# Local script API profile (Windows)

## Full API profile

An owner may explicitly create a separate **full-api** profile for local
scripts. It has the same effective authority as the authenticated Pan user,
including operations outside the rotation watcher; use it only for trusted
scripts. No full profile exists by default. Existing fixed and Codex-scoped
profiles keep their rules and are never implicitly promoted.

Run the following as the same Windows user as Pan, using its data directory:

```powershell
python -m packages.web.script_full_api init --data-dir <pan-data-dir> --profile my-script
```

The helper prompts for the Pan administrator password without putting it in
arguments, creates the usual owner-only script credential, and logs in through
the existing UserAuth service. The resulting separate UserAuth session is
encrypted with user-scoped DPAPI in the private profile directory. Requests
must pass both the local-only script pipe's profile check and the normal ASGI
user authentication and endpoint gates. No administrator browser token is
needed. A password change or UserAuth session revocation also invalidates the
profile's UserAuth session. A session may expire (normally after 12 hours if
the user session duration is unset); renew interactively with:

```powershell
python -m packages.web.script_full_api renew --data-dir <pan-data-dir> --profile my-script
```

From the reviewed checkout, an external Python script can use the existing
local instance locator without accessing a token directly:

```python
import json
from packages.web.script_full_api import request

reply = request(r'C:\path\to\pan-data', 'my-script', 'GET', '/api/automation/authorization')
print(reply['status'], json.loads(reply['content']))
reply = request(r'C:\path\to\pan-data', 'my-script', 'POST', '/api/sessions',
                json.dumps({'name': 'Example', 'adapter': 'codex'}).encode(),
                {'Content-Type': 'application/json'})
print(reply['status'], json.loads(reply['content']))
```

The selected `GET`, `HEAD`, `POST`, `PUT`, `PATCH`, `DELETE` and `OPTIONS`
methods dispatch to the normal `/api/` routes, with query strings and byte
bodies (including multipart). The return value contains the actual HTTP status,
body bytes, and selected response headers. The existing IPC frame bounds each
request and response body to 1,200,000 bytes; there is no streaming, WebSocket,
redirect following, non-API routing or TCP listener. Authorization, Cookie,
Origin, Host, hop-by-hop, proxy and caller identity headers are not accepted;
the lifecycle expected-instance headers are allowed. Endpoints requiring an
excluded header retain their normal denial; API routes that require a session
or an Origin beyond ordinary UserAuth may therefore not be usable through this
transport. HTTP 401/403 and application errors remain real responses, not
success. If a response is lost or exceeds the IPC limit, an earlier write may
already have occurred; check the original operation before any retry. No profile credential or user session is passed in argv, general Job
environment, logs or child process environment.

Revoke with `python -m packages.web.script_auth revoke --data-dir <pan-data-dir>
--profile my-script`. This disables the profile and logs out its UserAuth
session; in-flight calls may already have passed authentication. If setup is
interrupted, inspect the private profile directory rather than automatically
overwriting an existing identity.

## Scoped watcher profile

This optional profile is disabled until an owner creates it. It uses the
dedicated local-only IPC, not the general plugin pipe, a network HTTP credential or browser login.
It does not change Agent, Browser, QQ or WeChat permissions. The supported
secret storage is Windows user-scoped DPAPI and owner-only ACL; other platforms
fail closed. As with the existing local IPC and DPAPI contract, this isolates
other OS users, not malicious processes running as the same user. Do not expose
the pipe or locator through a reverse proxy or remote transport. Each Windows
pipe instance is created with `PIPE_REJECT_REMOTE_CLIENTS` and an owner-only
security descriptor. The general plugin pipe rejects the script operation.

Create an owner-controlled policy JSON file outside Git, using exact Session
IDs appropriate to this particular watcher. The list below is the complete
watcher set; remove writes or individual reads that are not needed. A Session
list cannot be replaced by a wildcard.

```json
{
  "enabled": true,
  "sessions": ["ses_replace_with_owned_id"],
  "rules": [
    {"method":"GET", "path":"/api/automation/events"},
    {"method":"GET", "path":"/api/list"},
    {"method":"GET", "path":"/api/sessions"},
    {"method":"GET", "path":"/api/sessions/{sessionId}"},
    {"method":"GET", "path":"/api/sessions/{sessionId}/worker/delayed-restart"},
    {"method":"POST", "path":"/api/sessions/{sessionId}/worker/delayed-restart"},
    {"method":"POST", "path":"/api/sessions/wake"}
  ]
}
```

For the rotation watcher, a separate explicit mode can include newly created
Codex Sessions without editing IDs. Replace the `sessions` line above with
`"sessions": [], "targetMode": "codex",` and retain only the enumerated
rules needed by the watcher. This grants those operations for **all** current
and future Sessions whose server registry adapter is Codex, including offline
ones; the watcher itself selects online idle/running Workers and only wakes a
qualifying limit target. The server re-evaluates the registry on each request.
Other adapters and deleted Sessions are excluded. This mode cannot be combined
with fixed IDs, and it is not a general adapter selector or wildcard.

From the reviewed checkout, with an explicit Pan **data directory** and a
private policy file, initialize once as the OS user running Pan and the watcher:

```powershell
python -m packages.web.script_auth init --data-dir <pan-data-dir> --profile codex-watcher --policy <private-policy.json>
```

The helper creates a random credential without displaying it; the server-side
profile keeps its digest and the client file is DPAPI encrypted. Neither token
nor administrator Bearer belongs in argv, logs, Git or the Job environment.
Only the local owner should edit the private profile's policy. Changes are read
on each call, so disabling the profile or removing a rule takes effect on the
next request. Invalid settings fail closed. To revoke without finding a token:

```powershell
python -m packages.web.script_auth revoke --data-dir <pan-data-dir> --profile codex-watcher
```

The standalone watcher explicitly selects IPC instead of HTTP:

```powershell
python scripts/codex_limit_rotate.py --script-profile codex-watcher --script-data-dir <pan-data-dir> --ledger <private-ledger.json> --bridge-data <cc-switch-bridge-data>
```

It defaults to observation; `--execute` enables effects. An IPC denial never
falls back to the existing Bearer HTTP mode. Event reads are bounded and filter
out unbound/other-Session and non-structured-quota events while retaining the upstream cursor.
Only bounded identity and structured quota fields are returned; raw observation
and arbitrary event payload fields are excluded. Inventory
and details are restricted to the configured Sessions and projected to the
watcher's required fields. The IPC profile does not change cc-switch permissions
or prove Worker binding or Wake consumption. Review the retained ledger and
unknown effects before operating an existing watcher.
