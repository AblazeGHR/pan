"""Explicit user principal for owned HTTP business-regression fixtures.

Raw httpx calls and explicitly supplied headers remain unchanged so auth-denial,
logout/expiry and forged-origin assertions still exercise the real boundary.
"""
from packages.automation.authorization import object_path


def authenticate_user_requests(runtime):
    original=runtime.request
    cached={}
    def request(method,path,**kwargs):
        if (object_path(path) or path.startswith('/api/jobs/') or path.startswith('/api/scheduler/')) and 'headers' not in kwargs:
            pid=runtime.process.pid if runtime.process else None
            if cached.get('pid')!=pid:
                key=(runtime.data_root/'automation/user.key').read_text(encoding='utf-8')
                token=original('POST','/api/automation/login',json={'key':key})['token']
                cached.update(pid=pid,headers={'Authorization':'Bearer '+token})
            kwargs['headers']=cached['headers']
        return original(method,path,**kwargs)
    runtime.request=request
