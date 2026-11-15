"""Explicit fixture provisioning; never monkeypatch production authentication."""
import secrets
PASSWORD = 'Aa1!' + secrets.token_urlsafe(24)


def provision(auth):
    if not auth.path.exists():
        auth.initialize(PASSWORD)
    return PASSWORD
