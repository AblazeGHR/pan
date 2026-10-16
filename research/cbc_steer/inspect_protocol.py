from pathlib import Path
import re,json,hashlib
ROOT=Path(r'D:/node_npm/node_global/node_modules/@tencent-ai/codebuddy-code')
OUT=Path(__file__).parent
terms=['SteerInputBuffer','this.subtype="steer"','expectedRequestId','clientUserMessageId','ownerRequestId','steerInputBuffer','async function steer(','session/steer','control_response','async takeSteerContinuationInput(','async admitSteerToVisibleHistory(','function buildSteerInjectionInput(','MessageQueueRequestProcessor','useMessageSender','expectedRunId','server/admitSteer']
rows=[]
for rel in ['dist/codebuddy-headless.js','dist/codebuddy.js','server.cjs','dist-server/codebuddy.js']:
 s=(ROOT/rel).read_text(encoding='utf-8')
 rows.append({'file':rel,'sha256':hashlib.sha256(s.encode()).hexdigest()})
 for term in terms:
  matches=list(re.finditer(re.escape(term),s))
  rows.append({'term':term,'count':len(matches),'snippets':[{'offset':m.start(),'text':s[max(0,m.start()-350):m.start()+1700]} for m in matches[:8]]})
(OUT/'installed-protocol.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
for row in rows:
 if 'file' in row: print(row)
 elif row['count']: print(row['term'],row['count'])
