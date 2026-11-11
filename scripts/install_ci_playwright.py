"""Install full Playwright dependencies on a disposable GitHub Ubuntu runner.

Keep the first failure. Only an Ubuntu archive DNS failure allows one retry
against official archive/security sources; package and signature failures stop.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys


HOSTS = ('azure.archive.ubuntu.com', 'archive.ubuntu.com', 'security.ubuntu.com')
MIRROR_URI = 'mirror+file:/etc/apt/apt-mirrors.txt'
AZURE = re.compile(r'https?://azure\.archive\.ubuntu\.com/ubuntu/?(?=\s|$)')
DNS = re.compile(r"Temporary failure resolving ['\"](" + '|'.join(re.escape(h) for h in HOSTS) + r")[ '\"]")


def dns_failure_only(output):
    """APT emits multi-line errors; require every final fetch error to be DNS.

    Do not classify arbitrary pip/CDN failures, missing packages, bad signatures,
    permissions or dpkg problems as a mirror outage just because DNS also failed.
    """
    if not DNS.search(output):
        return False
    fetches = [line for line in output.splitlines() if line.startswith('E: Failed to fetch ')]
    if not fetches or any(not DNS.search(line) for line in fetches):
        return False
    warnings = [line for line in output.splitlines() if line.startswith('W:')]
    if any(not (DNS.search(line) or line.startswith('W: Some index files failed to download.'))
           for line in warnings):
        return False
    wrappers = [line for line in output.splitlines() if line.startswith('Error:')]
    if wrappers != ['Error: Installation process exited with code: 100']:
        return False
    errors = [line for line in output.splitlines() if line.startswith(('E:', 'dpkg:'))]
    return all(line.startswith('E: Failed to fetch ') or
               line.startswith('E: Unable to fetch some archives') for line in errors)


def fallback_sources(sources):
    """Preserve suites/components/options/Signed-By and all unrelated sources.

    Recognize both runner-image formats: deb822 URIs or legacy deb lines, plus
    the official runner mirror list. Unknown mirror lists fail without writing.
    """
    updates = {}
    uses_mirrors = any(MIRROR_URI in text for name, text in sources.items()
                       if name.endswith(('.sources', '.list')))
    if uses_mirrors:
        text = sources.get('apt-mirrors.txt')
        if text is None:
            raise ValueError('Runner mirror list is missing')
        urls = [line.split()[0] for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#')]
        allowed = {scheme + '://' + host + '/ubuntu' + slash
                   for scheme in ('http', 'https') for host in HOSTS for slash in ('', '/')}
        if not urls or any(url not in allowed for url in urls):
            raise ValueError('Unrecognized runner mirror list; refusing replacement')
        updates['apt-mirrors.txt'] = ('https://archive.ubuntu.com/ubuntu/\tpriority:1\n'
                                      'https://security.ubuntu.com/ubuntu/\tpriority:2\n')
    for name, text in sources.items():
        if not name.endswith(('.list', '.sources')):
            continue
        lines = []
        for line in text.splitlines(keepends=True):
            if re.match(r'^\s*(?:URIs:|deb(?:-src)?\s)', line):
                line = AZURE.sub('https://archive.ubuntu.com/ubuntu/', line)
            lines.append(line)
        changed = ''.join(lines)
        if changed != text:
            updates[name] = changed
    if not updates:
        raise ValueError('No recognized Azure or GitHub mirror source to replace')
    return updates


def runner_guard():
    if (sys.platform != 'linux' or platform.machine() not in ('x86_64', 'amd64') or
            os.environ.get('GITHUB_ACTIONS') != 'true' or
            os.environ.get('RUNNER_OS') != 'Linux' or
            os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted'):
        raise RuntimeError('This installer only modifies disposable GitHub-hosted Linux runners')
    if platform.freedesktop_os_release().get('ID') != 'ubuntu':
        raise RuntimeError('This installer requires Ubuntu')


def snapshot(apt_root):
    paths = [apt_root / 'sources.list', apt_root / 'apt-mirrors.txt']
    paths += sorted((apt_root / 'sources.list.d').glob('*.list'))
    paths += sorted((apt_root / 'sources.list.d').glob('*.sources'))
    return {str(p.relative_to(apt_root)): p.read_text(encoding='utf-8')
            for p in paths if p.is_file()}


def install_once(evidence, number):
    # GNU timeout bounds the complete installer process group, including apt.
    command = ['timeout', '--signal=TERM', '--kill-after=10s', '480s', sys.executable,
               '-m', 'playwright', 'install', '--with-deps', '--only-shell', 'chromium']
    path = evidence / f'install-{number}.log'
    print(f'Full Playwright installation attempt {number}: {command}', flush=True)
    with path.open('w', encoding='utf-8') as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=False)
    output = path.read_text(encoding='utf-8', errors='replace')
    print(output, flush=True)
    print(f'Playwright installation attempt {number} exit: {result.returncode}', flush=True)
    return result.returncode, output


def install(evidence, apt_root=Path('/etc/apt')):
    runner_guard()
    evidence.mkdir(parents=True, exist_ok=False)
    before = snapshot(apt_root)
    (evidence / 'sources-before.json').write_text(json.dumps(before, indent=2), encoding='utf-8')
    print('Original apt sources:\n' + json.dumps(before, indent=2), flush=True)
    code, output = install_once(evidence, 1)
    if code == 0:
        return 0
    # Timeout/termination, TLS, signatures and other failures are never retried.
    if code in (124, 137, 143) or not dns_failure_only(output):
        return code
    updates = fallback_sources(before)
    print('Recognized Ubuntu archive DNS failure; one official-source retry', flush=True)
    print('Source changes:\n' + json.dumps(updates, indent=2), flush=True)
    (evidence / 'source-changes.json').write_text(json.dumps(updates, indent=2), encoding='utf-8')
    for name, text in updates.items():
        subprocess.run(['sudo', '-n', 'tee', str(apt_root / name)],
                       input=text, text=True, stdout=subprocess.DEVNULL, check=True)
    (evidence / 'sources-after.json').write_text(json.dumps(snapshot(apt_root), indent=2), encoding='utf-8')
    code, _ = install_once(evidence, 2)
    return code


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    args = parser.parse_args()
    sys.exit(install(args.evidence_dir))
