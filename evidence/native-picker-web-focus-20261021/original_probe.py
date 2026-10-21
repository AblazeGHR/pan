"""Run the original fixed helper through the same production Web click path."""
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[2]
target=ROOT/'packages/web/native_file_dialog.ps1'
current=target.read_bytes()
expected=subprocess.check_output(['git','show','8ddb45ed:packages/web/native_file_dialog.ps1'],cwd=ROOT)
assert current.replace(b'\r\n',b'\n')==expected.replace(b'\r\n',b'\n')
baseline=subprocess.check_output(['git','show','0a2e5f04:packages/web/native_file_dialog.ps1'],cwd=ROOT)
try:
 target.write_bytes(baseline)
 result=subprocess.run([sys.executable,str(Path(__file__).with_name('web_probe.py')),'mouse-original'],cwd=ROOT)
finally:
 target.write_bytes(current)
assert target.read_bytes()==current
sys.exit(result.returncode)
