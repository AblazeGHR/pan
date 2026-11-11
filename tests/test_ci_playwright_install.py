"""DNS-only runner fallback keeps full installation and apt trust semantics."""
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest
import yaml

from scripts import install_ci_playwright as installer


MIRRORS = ('http://azure.archive.ubuntu.com/ubuntu/\tpriority:1\n'
           'https://archive.ubuntu.com/ubuntu/\tpriority:2\n'
           'https://security.ubuntu.com/ubuntu/\tpriority:3\n')
SOURCE = ('Types: deb\nURIs: mirror+file:/etc/apt/apt-mirrors.txt\n'
          'Suites: noble noble-updates noble-backports noble-security\n'
          'Components: main restricted universe multiverse\n'
          'Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n')
DNS_LOG = ("E: Failed to fetch https://security.ubuntu.com/ubuntu/pool/font.deb "
           "Temporary failure resolving 'azure.archive.ubuntu.com'\n"
           'E: Unable to fetch some archives, maybe run apt-get update\n'
           'Failed to install browsers\nError: Installation process exited with code: 100\n')
TRANSCRIPT = Path('tests/fixtures/ci_playwright_ubuntu_dns_attempt2.json')


def test_classifier_accepts_original_ci_transcript_and_proves_old_deadline_truncates_it():
    events = json.loads(TRANSCRIPT.read_text(encoding='utf-8'))['events']
    whole = '\n'.join(event['message'] for event in events)
    old_deadline = '\n'.join(event['message'] for event in events if event['elapsedSeconds'] <= 480)
    assert 600 < events[-1]['elapsedSeconds'] < installer.INSTALL_TIMEOUT_SECONDS
    assert "Temporary failure resolving 'azure.archive.ubuntu.com'" in old_deadline
    assert not installer.dns_failure_only(old_deadline)
    assert installer.dns_failure_only(whole)
    assert not installer.dns_failure_only(whole + '\nE: The repository is not signed.\n')


@pytest.mark.skipif(sys.platform != 'linux', reason='Real GNU timeout process-group replay requires Linux')
@pytest.mark.parametrize('deadline,expected_calls,expected_code', [(480, [1], 124), (900, [1, 2], 0)])
def test_slow_original_dns_replay_uses_complete_errors_before_retry(
        tmp_path, monkeypatch, deadline, expected_calls, expected_code):
    # Replay the original ~605-second timeline at 100x speed through real GNU
    # timeout, not an immediate fabricated CompletedProcess or synthetic log.
    original_run = installer.subprocess.run
    original_once = installer.install_once
    root, calls, writes = fixture_runner(tmp_path, monkeypatch, [])
    sudo = installer.subprocess.run
    replay = tmp_path / 'replay.py'
    replay.write_text('''import json,sys,time
events=json.load(open(sys.argv[1]))['events']
start=time.monotonic()
for event in events:
    time.sleep(max(0,event['elapsedSeconds']/100-(time.monotonic()-start)))
    print(event['message'],flush=True)
raise SystemExit(1)
''')
    monkeypatch.setattr(installer, 'INSTALL_TIMEOUT_SECONDS', deadline)
    def execute(command, **kwargs):
        if command[0] != 'timeout':
            return sudo(command, **kwargs)
        calls.append(len(calls) + 1)
        assert command[5:] == ['-m', 'playwright', 'install', '--with-deps', '--only-shell', 'chromium']
        if len(calls) == 2:
            kwargs['stdout'].write('second full installation succeeded')
            return SimpleNamespace(returncode=0)
        actual = [*command[:3], f'{deadline/100}s', sys.executable,
                  str(replay), str(TRANSCRIPT.resolve())]
        return original_run(actual, **kwargs)
    monkeypatch.setattr(installer.subprocess, 'run', execute)
    monkeypatch.setattr(installer, 'install_once', original_once)
    evidence = tmp_path / 'evidence'
    assert installer.install(evidence, root) == expected_code
    assert calls == expected_calls
    output = (evidence / 'install-1.log').read_text()
    assert installer.dns_failure_only(output) == (deadline == 900)
    assert bool(writes) == (deadline == 900)
    assert json.loads((evidence / 'sources-before.json').read_text())['apt-mirrors.txt'] == MIRRORS


@pytest.mark.skipif(sys.platform != 'linux', reason='Real GNU timeout process-group cleanup requires Linux')
def test_install_timeout_terminates_its_owned_child_group(tmp_path, monkeypatch):
    original_run = installer.subprocess.run
    proof = tmp_path / 'child-ended'
    script = tmp_path / 'blocked.py'
    script.write_text('''import signal,subprocess,sys,time
from pathlib import Path
child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])
def terminate(*_):
    try:
        status=child.wait(timeout=2)
        assert status == -signal.SIGTERM, status
        Path(sys.argv[1]).write_text(str(status))
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
    raise SystemExit(0)
signal.signal(signal.SIGTERM,terminate)
time.sleep(30)
''')
    def execute(command, **kwargs):
        assert command[:4] == ['timeout', '--signal=TERM', '--kill-after=10s', '900s']
        return original_run([*command[:3], '2s', sys.executable, str(script), str(proof)], **kwargs)
    monkeypatch.setattr(installer.subprocess, 'run', execute)
    code, output = installer.install_once(tmp_path, 1)
    assert code == 124 and not installer.dns_failure_only(output)
    assert proof.read_text() == '-15'


def test_runner_mirror_fallback_preserves_deb822_and_third_party_sources():
    sources = {'apt-mirrors.txt': MIRRORS, 'sources.list.d/ubuntu.sources': SOURCE,
               'sources.list.d/microsoft.list': 'deb [arch=amd64] https://packages.microsoft.com/repos/code stable main\n'}
    original = dict(sources)
    assert installer.fallback_sources(sources) == {'apt-mirrors.txt':
        'https://archive.ubuntu.com/ubuntu/\tpriority:1\nhttps://security.ubuntu.com/ubuntu/\tpriority:2\n'}
    assert sources == original


@pytest.mark.parametrize('name,text', [
    ('sources.list', 'deb [signed-by=/keyring.gpg] http://azure.archive.ubuntu.com/ubuntu/ jammy main universe\n'),
    ('sources.list.d/ubuntu.sources', SOURCE.replace(installer.MIRROR_URI, 'http://azure.archive.ubuntu.com/ubuntu/'))])
def test_direct_azure_sources_keep_all_non_uri_fields(name, text):
    assert installer.fallback_sources({name: text}) == {
        name: text.replace('http://azure.archive.ubuntu.com/ubuntu/', 'https://archive.ubuntu.com/ubuntu/')}


@pytest.mark.parametrize('mirrors', [None, '', 'https://untrusted.invalid/ubuntu/\n'])
def test_unknown_mirror_setup_cannot_be_replaced(mirrors):
    sources = {'sources.list.d/ubuntu.sources': SOURCE}
    if mirrors is not None:
        sources['apt-mirrors.txt'] = mirrors
    with pytest.raises(ValueError):
        installer.fallback_sources(sources)


@pytest.mark.parametrize('output', [
    'Error: Download chromium failed: Temporary failure resolving example.com',
    DNS_LOG.replace('azure.archive.ubuntu.com', 'untrusted.invalid'),
    DNS_LOG + 'E: The repository is not signed.\n',
    DNS_LOG + 'W: GPG error: invalid signature\n',
    DNS_LOG + 'E: Unable to locate package libfoo\n',
    DNS_LOG + 'E: Failed to fetch https://archive.ubuntu.com/x.deb 404 Not Found\n',
    DNS_LOG + 'dpkg: error processing package\n',
    DNS_LOG + 'E: Could not open lock file: Permission denied\n',
    DNS_LOG + 'E: Failed to fetch https://archive.ubuntu.com/y.deb Certificate verification failed\n'])
def test_non_dns_and_mixed_failures_are_not_retry_permission(output):
    assert not installer.dns_failure_only(output)


@pytest.mark.parametrize('extra', [
    'W: Failed to fetch https://archive.ubuntu.com/InRelease Certificate verification failed\n',
    'W: An error occurred during the signature verification\n',
    'Error: Download chromium failed\n'])
def test_dns_does_not_hide_other_update_or_browser_errors(extra):
    assert not installer.dns_failure_only(DNS_LOG + extra)


def fixture_runner(tmp_path, monkeypatch, outcomes):
    root = tmp_path / 'apt'
    (root / 'sources.list.d').mkdir(parents=True)
    (root / 'apt-mirrors.txt').write_text(MIRRORS)
    (root / 'sources.list.d/ubuntu.sources').write_text(SOURCE)
    calls, writes = [], []
    monkeypatch.setattr(installer, 'runner_guard', lambda: None)
    def once(evidence, number):
        calls.append(number)
        code, output = outcomes[number - 1]
        (evidence / f'install-{number}.log').write_text(output)
        return code, output
    monkeypatch.setattr(installer, 'install_once', once)
    def sudo(command, *, input, **kwargs):
        assert command[:3] == ['sudo', '-n', 'tee'] and kwargs['check'] is True
        target = Path(command[3])
        assert target.is_relative_to(root)
        writes.append(target)
        target.write_text(input)
    monkeypatch.setattr(installer.subprocess, 'run', sudo)
    return root, calls, writes


@pytest.mark.parametrize('second_code', [0, 1])
def test_dns_failure_preserved_and_exactly_one_full_retry(tmp_path, monkeypatch, second_code):
    root, calls, writes = fixture_runner(tmp_path, monkeypatch, [(1, DNS_LOG), (second_code, 'second installation')])
    evidence = tmp_path / 'evidence'
    assert installer.install(evidence, root) == second_code
    assert calls == [1, 2] and writes == [root / 'apt-mirrors.txt']
    assert (evidence / 'install-1.log').read_text() == DNS_LOG
    assert json.loads((evidence / 'sources-before.json').read_text())['apt-mirrors.txt'] == MIRRORS
    assert (root / 'sources.list.d/ubuntu.sources').read_text() == SOURCE
    assert 'azure.archive' not in (root / 'apt-mirrors.txt').read_text()


@pytest.mark.parametrize('code,output', [(0, 'success'), (1, 'invalid signature'), (124, DNS_LOG)])
def test_success_non_dns_and_timeout_never_mutate_or_retry(tmp_path, monkeypatch, code, output):
    root, calls, writes = fixture_runner(tmp_path, monkeypatch, [(code, output)])
    assert installer.install(tmp_path / 'evidence', root) == code
    assert calls == [1] and writes == []
    assert (root / 'apt-mirrors.txt').read_text() == MIRRORS


def test_installer_runs_full_command_and_retains_its_exit(tmp_path, monkeypatch):
    def run(command, *, stdout, **kwargs):
        assert command[:4] == ['timeout', '--signal=TERM', '--kill-after=10s', '900s']
        assert command[5:] == ['-m', 'playwright', 'install', '--with-deps', '--only-shell', 'chromium']
        stdout.write('real installer output')
        assert kwargs['check'] is False
        return SimpleNamespace(returncode=17)
    monkeypatch.setattr(installer.subprocess, 'run', run)
    assert installer.install_once(tmp_path, 1) == (17, 'real installer output')


def test_installer_refuses_non_hosted_environment_before_any_write(tmp_path, monkeypatch):
    monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
    with pytest.raises(RuntimeError, match='disposable GitHub-hosted'):
        installer.install(tmp_path / 'evidence', tmp_path / 'apt')
    assert list(tmp_path.iterdir()) == []


def test_workflow_only_wires_guarded_installer_into_ubuntu_browser():
    workflow = yaml.safe_load(Path('.github/workflows/test.yml').read_text(encoding='utf-8'))
    matching = [(job, step) for job, value in workflow['jobs'].items()
                for step in value['steps'] if 'scripts/install_ci_playwright.py' in step.get('run', '')]
    assert len(matching) == 1 and matching[0][0] == 'ubuntu-browser'
    assert matching[0][1]['run'] == ('python scripts/install_ci_playwright.py --evidence-dir '
                                     'evidence/cp-x1-ubuntu-browser/dependencies')
    steps = workflow['jobs']['ubuntu-browser']['steps']
    assert any('tests/test_job_maintenance_browser.py' in s.get('run', '') for s in steps)
    assert any('action-p6-ui-compat.e2e.mjs' in s.get('run', '') for s in steps)
    assert any(s.get('if') == 'always()' and 'evidence/cp-x1-ubuntu-browser/' in s.get('with', {}).get('path', '') for s in steps)
