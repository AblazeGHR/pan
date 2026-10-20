"""Windowless, one-shot login entry; delegates all service semantics to launcher."""
import argparse
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--python', required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if not Path(args.python).is_absolute():
        raise RuntimeError('Login interpreter must be absolute')
    os.chdir(root)
    sys.path.insert(0, str(root))
    # A login launch owns this installation's defaults, never an inherited
    # Agent/Job instance's port, identity, or data-directory overrides.
    for key in tuple(os.environ):
        if key.upper().startswith('PAN_'):
            os.environ.pop(key)
    # Pin a validated absolute interpreter; no login-shell PATH discovery.
    os.environ['PAN_PYTHON'] = str(Path(args.python).resolve())
    from packages.core import launcher
    log = root / 'data/logs/login-startup.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('a', encoding='utf-8') as handle:
        sys.stdout = sys.stderr = handle
        try:
            launcher.start_service(root, hidden=True, python_argv=[os.environ['PAN_PYTHON']])
        except launcher.DuplicateStart:
            return 0
        except Exception as exc:
            print(f'Login startup failed: {exc}')
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
