"""Repeat baseline environment failures after installing/building its own web tree."""
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

out = Path(__file__).resolve().parent
repo = out.parents[1].parent / 'job-p7a-baseline-7f303faa-20261026'
nodes = []
for test in ET.parse(out / 'baseline-qa.xml').iter('testcase'):
    if test.find('failure') is not None or test.find('error') is not None:
        nodes.append(test.attrib['classname'].replace('.', '/') + '.py::' + test.attrib['name'])
nodes.append('tests/test_mcp_handbook.py::TestDescriptionCallChain::test_every_tool_ends_with_pan_skill_pointer')
command = [sys.executable, str(repo / 'scripts/run_tests_isolated.py'), '--', sys.executable,
           '-m', 'pytest', *nodes, '-q', '-o', 'addopts=', '--tb=short',
           '--junitxml=' + str(out / 'baseline-retry.xml')]
(out / 'baseline-retry-command.json').write_text(json.dumps(command, indent=2), encoding='utf-8')
with (out / 'baseline-retry.log').open('w', encoding='utf-8') as log:
    result = subprocess.run(command, cwd=repo, stdin=subprocess.DEVNULL,
                            stdout=log, stderr=subprocess.STDOUT)
(out / 'baseline-retry-exit.json').write_text(json.dumps({'exitCode': result.returncode,
    'sameIdentities': len(nodes)}, indent=2), encoding='utf-8')
sys.exit(result.returncode)
