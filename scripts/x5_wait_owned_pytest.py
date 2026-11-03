"""Bounded observation of one already-started owned pytest; never launch it again."""
import argparse,json,time
from pathlib import Path
import psutil
p=argparse.ArgumentParser();p.add_argument('--pid',type=int,required=True);p.add_argument('--birth',type=float,required=True);p.add_argument('--junit',type=Path,required=True);a=p.parse_args()
deadline=time.monotonic()+1200
while time.monotonic()<deadline:
    try:
        process=psutil.Process(a.pid)
        if process.create_time()!=a.birth:break
        process.wait(timeout=min(5,max(.01,deadline-time.monotonic())))
        break
    except psutil.TimeoutExpired:continue
    except psutil.NoSuchProcess:break
else:
    raise SystemExit('original owned pytest still running; outcome unknown; no replay')
if not a.junit.is_file():raise SystemExit('owned pytest ended without JUnit; outcome unknown')
import xml.etree.ElementTree as E
root=E.parse(a.junit).getroot()
failed=any(c.find('failure') is not None or c.find('error') is not None for c in root.iter('testcase'))
print(json.dumps({'junit':str(a.junit),'failure':failed}))
raise SystemExit(int(failed))
