"""Recover this completed trial's native history after its summary writer failed.
Does not start a CLI/model or terminate processes. Reports missing FILETIME honestly.
"""
import json,re,subprocess
from pathlib import Path
OUT=Path(__file__).resolve().parent
base=OUT/'tui-isolated-7b0d8ed5c8'
paths=list((base/'profile/projects').rglob('3338ac56-cc78-4e5f-9eef-3b646241d5dc.jsonl'));assert len(paths)==1
rows=[json.loads(line) for line in paths[0].read_text(encoding='utf-8').splitlines()]
users=[row for row in rows if row.get('role')=='user']
assert len(users)==2
original,steer=users
assert 'TUI_BASELINE_19' in json.dumps(original['content'])
assert 'TUI_STEER_CONSUMED_73' in json.dumps(steer['content'])
run=original['providerData']['conversationRequestId']
assert steer['providerData']['conversationRequestId']==run
assert steer['providerData']['startsNewUserRequest'] is False
assistant=next(row for row in rows if row.get('role')=='assistant' and 'TUI_STEER_CONSUMED_73' in json.dumps(row.get('content')))
assert assistant['providerData']['conversationRequestId']==run
log=next((base/'profile/logs').rglob('*.log')).read_text(encoding='utf-8',errors='replace')
boot=re.search(r'cli onStart pid=(\d+) version=([\d.]+)',log);assert boot
root_pid=int(boot[1]);observed_pids={int(x) for x in re.findall(r'\bpid=(\d+)',log) if int(x)>0}
import psutil
live=[]
for pid in observed_pids:
 try:
  process=psutil.Process(pid)
  live.append({'pid':pid,'createdAt':process.create_time(),'name':process.name(),'identityMatchesOriginal':'unknown; no termination authorized by pid alone'})
 except psutil.NoSuchProcess:pass
evidence={'recovered':True,'sessionId':original['sessionId'],'conversationRequestId':run,'cliVersion':boot[2],'rootPidFromDedicatedLog':root_pid,'rootCreationFiletime':None,'sameRunConsumed':True,'startsNewUserRequest':False,'persistentSteerMessageId':steer['id'],'persistentAssistantMessageId':assistant['id'],'finalAssistantText':'TUI_STEER_CONSUMED_73','nativeHistoryPath':str(paths[0]),'originalJobStatus':'failed','failure':'BackendCloseError: root retained handle not yet confirmed dead when close was called','lostEvidence':['retained root FILETIME summary','observer hook event summary','terminal raw output'],'dedicatedLogObservedPids':sorted(observed_pids),'currentlyLiveObservedPids':live,'rootCurrentlyAbsent':root_pid not in [x['pid'] for x in live],'modelRerun':False,'cleanupClaim':'original stack proves terminate_tree returned no remaining members before close; no successful close report was persisted'}
(OUT/'recovered-tui-evidence.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
print(json.dumps(evidence))
