"""Archive raw evidence, normalize readable whitespace, hash final artifacts."""
import hashlib
import json
from pathlib import Path
import socket
import zipfile

import psutil

out=Path(__file__).resolve().parent
evidence=json.loads((out/'http-evidence.json').read_text(encoding='utf-8'))
port=evidence['port']
assert port not in {8767,8768}
with socket.socket() as sock:
    sock.settimeout(.3)
    released=sock.connect_ex(('127.0.0.1',port))!=0
assert released
pids={evidence['identity']['pid'],*[n['pid'] for n in evidence['actualFakeCLIReceived']]}
observed=[]
for pid in pids:
    try:
        p=psutil.Process(pid)
        argv=p.cmdline()
        is_test=any('job_inbox_http_server.py' in s or 'fake_stream_cli.py' in s for s in argv)
        observed.append({'pid':pid,'running':True,'ownedTestCommand':is_test,'createTime':p.create_time()})
        assert not is_test, 'Owned test process remains alive'
    except psutil.NoSuchProcess:
        observed.append({'pid':pid,'running':False})
(out/'cleanup.json').write_text(json.dumps({'port':port,'portReleased':released,'processes':observed},indent=2)+'\n',encoding='utf-8')
raw=[*out.glob('*.log'),*out.glob('*.xml')]
with zipfile.ZipFile(out/'raw-evidence.zip','a',zipfile.ZIP_DEFLATED) as archive:
    for p in raw:
        if p.name not in archive.namelist():
            archive.write(p,p.name)
for p in raw:
    content='\n'.join(line.rstrip() for line in p.read_text(encoding='utf-8-sig').splitlines()).rstrip()
    p.write_text(content+'\n' if content else '',encoding='utf-8')
hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir() if p.is_file() and p.name!='sha256.json'}
(out/'sha256.json').write_text(json.dumps(hashes,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'portReleased':released,'processes':observed,'artifacts':len(hashes)}))
