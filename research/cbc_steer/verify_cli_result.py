"""Validate only this experiment's retained frames and dedicated native JSONL."""
import json
from pathlib import Path
OUT=Path(__file__).resolve().parent
facts=json.loads((OUT/'cli-result.json').read_text(encoding='utf-8'))
probe=facts['probe'];sid=facts['cliSessionId'];frames=probe['frames']
result=next(x for x in frames if x.get('type')=='result')
run=result['_requestId']
assert result['result']=='STEER_CONSUMED_73' and result['is_error'] is False
reply=next(x for x in frames if x.get('type')=='control_response' and x.get('response',{}).get('request_id')=='isolated-steer-1')
assert reply['response']['response']['steered'] is True
assert reply['response']['response']['session_id']==sid
assert {x['_requestId'] for x in frames if x.get('_requestId')}=={run}
assert all(x.get('session_id',sid)==sid for x in frames)
base=Path(facts['base']).resolve();assert base.is_relative_to(OUT)
paths=list((base/'profile/projects').rglob(sid+'.jsonl'));assert len(paths)==1
rows=[json.loads(line) for line in paths[0].read_text(encoding='utf-8').splitlines()]
steer=next(x for x in rows if x.get('role')=='user' and 'Change the final marker' in json.dumps(x.get('content')))
assert steer['providerData']['startsNewUserRequest'] is False
assert steer['providerData']['conversationRequestId']==run
assert steer['providerData']['rootRequestId']==run
assistant=next(x for x in rows if x.get('role')=='assistant' and 'STEER_CONSUMED_73' in json.dumps(x.get('content')))
assert assistant['providerData']['conversationRequestId']==run
assert any(x['pid']==probe['identity']['pid'] and x['ownedJobEmpty'] for x in facts['notes'])
evidence={'cliVersion':'2.164.0','identity':probe['identity'],'sessionId':sid,'conversationRequestId':run,'accepted':True,'sameRunConsumed':True,'persistentSteerMessageId':steer['id'],'persistentAssistantMessageId':assistant['id'],'startsNewUserRequest':False,'finalResult':result['result'],'ownedJobEmpty':True,'nativeHistoryPath':str(paths[0]),'streamConsumptionReceipt':False,'modelRequestsObserved':sum(x.get('type')=='assistant' for x in frames),'usage':result['usage']}
(OUT/'verified-cli-evidence.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
print(json.dumps(evidence))
