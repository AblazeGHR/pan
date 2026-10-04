import threading

import pytest

from packages.core.terminal.attachments import AttachmentRegistry
from packages.core.terminal.contracts import StaleLeaseError


def test_durable_revocation_does_not_tombstone_terminal_or_revive_tokens():
    class Target:
        def write(self, data):
            return len(data)

    target = Target()
    registry = AttachmentRegistry(lambda _: target)
    old_control = registry.attach('terminal', 'old', role='control')
    old_observer = registry.attach('terminal', 'old')
    registry.revoke_all('terminal')
    assert registry.control_holder('terminal') is None
    for old in (old_control, old_observer):
        with pytest.raises(StaleLeaseError):
            registry.validate(old)
    fresh = registry.attach('terminal', 'new', role='control')
    assert fresh.generation > old_control.generation
    assert registry.send(fresh, b'new') == 3
    with pytest.raises(StaleLeaseError):
        registry.send(old_control, b'old')


def test_durable_revocation_serializes_after_accepted_write():
    entered, release, revoked = (threading.Event() for _ in range(3))

    class Target:
        def write(self, data):
            entered.set()
            assert release.wait(3)
            return len(data)

    registry = AttachmentRegistry(lambda _: Target())
    token = registry.attach('terminal', 'controller', role='control')
    writer = threading.Thread(target=lambda: registry.send(token, b'old'))
    revoke = threading.Thread(target=lambda: (registry.revoke_all('terminal'), revoked.set()))
    try:
        writer.start()
        assert entered.wait(2)
        revoke.start()
        assert not revoked.wait(.05)
    finally:
        release.set()
        writer.join(3)
        if revoke.ident is not None:
            revoke.join(3)
    assert not writer.is_alive() and not revoke.is_alive()
    assert revoked.is_set()
    with pytest.raises(StaleLeaseError):
        registry.send(token, b'late')
