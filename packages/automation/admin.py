"""Controlled local provisioning: no password argv, HTTP bootstrap or secret output."""
import argparse
from getpass import getpass
from pathlib import Path

from .auth import UserAuth


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--auth-root', type=Path, required=True,
                        help='Explicit automation authentication directory for this instance')
    args = parser.parse_args()
    password = getpass('New administrator password: ')
    if password != getpass('Confirm password: '):
        parser.error('Password confirmation does not match')
    try:
        UserAuth(args.auth_root.resolve()).initialize(password)
    except FileExistsError:
        parser.error('Administrator already configured; use the authenticated password change')
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print('Administrator configured. Sign in through the normal Pan URL.')


if __name__ == '__main__':
    main()
