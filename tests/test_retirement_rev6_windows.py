"""Canonical public runner successors of owned Windows GUI and file IPC.

This Windows acceptance file is selected only on the present Windows host.
No skip makes a capability claim. Helpers launch only self-owned processes.
"""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.test_job_process_logs import live, start, terminal, all_bytes, wait  # noqa: F401


@pytest.mark.parametrize('mode', ['new', 'alloc'])
def test_canonical_three_generation_gui_console_private_desktop_cleanup(live, mode):
    assert os.name == 'nt'
    c = live
    probe = Path(__file__).parent / 'helpers/desktop_tree_probe.py'
    argv = [sys.executable, str(probe.resolve()), '2', mode, str(c.tmp)]
    start(c, 'import subprocess; raise SystemExit(subprocess.call(' + repr(argv) + '))')
    doc = terminal(c)
    facts = [json.loads((c.tmp / f'{depth}.json').read_text()) for depth in range(3)]
    assert len({r['desktop'] for r in facts}) == 1
    for row in facts:
        assert row['desktop'] != row['inputDesktop'] and row['inputOwnedWindows'] == []
        assert row['gui']['creationFiletime'] == row['creationFiletime']
        if row['console']:
            assert row['console']['desktop'] == row['desktop']
    raw, _ = all_bytes(c)
    assert raw.count(b'probe') == 3
    from tests.test_job_hidden_production import released
    released(facts[0]['desktop'])
    assert doc['result']['cleanupProof']['ownedTree']['confirmed']


def test_canonical_bidirectional_file_ipc_stdout_stderr_exit7_cold_log(live):
    c = live
    # The existing fixture places the owned business under c.tmp and passes
    # cwd through the product process Action. There is no legacy runner.
    code = """import os,sys,json,time
from pathlib import Path
print('owned stdout',flush=True)
print('owned stderr',file=sys.stderr,flush=True)
Path('request.tmp').write_text('child-request')
os.replace('request.tmp','request.txt')
end=time.monotonic()+12
while not Path('reply.txt').exists():
 assert time.monotonic()<end
 time.sleep(.01)
assert Path('reply.txt').read_text()=='parent-reply'
Path('cwd.json').write_text(json.dumps(os.getcwd()))
raise SystemExit(7)
"""
    start(c, code)
    wait(lambda: (c.tmp / 'request.txt').exists(), bool)
    assert (c.tmp / 'request.txt').read_text() == 'child-request'
    (c.tmp / 'reply.tmp').write_text('parent-reply')
    os.replace(c.tmp / 'reply.tmp', c.tmp / 'reply.txt')
    doc = terminal(c, 'failed')
    assert doc['result']['receipt']['exitCode'] == 7
    assert Path(json.loads((c.tmp / 'cwd.json').read_text())) == c.tmp
    raw, _ = all_bytes(c)
    assert b'owned stdout' in raw and b'owned stderr' in raw
    original = c.service.final.get(c.job['id'])
    asyncio.run(c.service.recover_external_receipts())
    assert c.service.final.get(c.job['id']) == original
    assert all_bytes(c)[0] == raw
