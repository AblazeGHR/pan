"""Explicit real authority for business-route tests without production lifespan."""
from types import SimpleNamespace

from packages.automation.auth import UserAuth
from tests.support.auth_credentials import provision


def owned_bearer(app, root, monkeypatch):
    auth = UserAuth(root / 'automation')
    token = auth.login(provision(auth))
    monkeypatch.setattr(app.state, 'automation', SimpleNamespace(auth=auth), raising=False)
    return {'Authorization': 'Bearer ' + token}
