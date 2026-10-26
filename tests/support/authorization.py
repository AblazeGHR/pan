"""Explicit user principal for owned HTTP business-regression fixtures.

Raw httpx calls and explicitly supplied headers remain unchanged so auth-denial,
logout/expiry and forged-origin assertions still exercise the real boundary.

The injected surface is the *user* HTTP surface and is deliberately wider than
``packages.automation.authorization.object_path``: that allowlist describes what
the trusted MCP bridge may address, not which retained HTTP routes a real user
still calls.  Coupling the two revoked the user's own routes whenever a legacy
write address was retired from the bridge.
"""

from packages.automation.authorization import object_path

#: Retained HTTP families an owned user principal drives directly.
USER_SURFACE = (
    '/api/jobs',
    '/api/scheduler/',
    '/api/actions',
    '/api/action-drafts',
    '/api/automation',
    '/api/background-jobs',
    '/api/triggers',
    '/api/event-creators',
    '/api/job-templates',
    '/api/job-runs',
    '/api/operations',
)


def user_surface(path):
    """Whether an owned user Bearer is expected for this HTTP path."""
    return bool(object_path(path)) or path.startswith(USER_SURFACE)


def authenticate_user_requests(runtime):
    original=runtime.request
    cached={}
    def request(method,path,**kwargs):
        if user_surface(path) and 'headers' not in kwargs:
            pid=runtime.process.pid if runtime.process else None
            if cached.get('pid')!=pid:
                key=(runtime.data_root/'automation/user.key').read_text(encoding='utf-8')
                token=original('POST','/api/automation/login',json={'key':key})['token']
                cached.update(pid=pid,headers={'Authorization':'Bearer '+token})
            kwargs['headers']=cached['headers']
        return original(method,path,**kwargs)
    runtime.request=request
