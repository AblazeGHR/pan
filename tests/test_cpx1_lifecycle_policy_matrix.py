"""Complete the original Exit/Restart policy cross-product on canonical plans."""
import pytest

from packages.core import platform
from tests import test_lifecycle_canonical_preparation as preparation
from tests.test_lifecycle_canonical_preparation import prepared


@pytest.mark.skipif(not platform.supports('service.lifecycle'), reason='positive lifecycle effect requires host capability')
@pytest.mark.parametrize('operation', ['exit', 'restart'])
@pytest.mark.parametrize('strategy,choice,mark', [
    ('ask', True, True),
    ('ask', False, False),
    ('offline', False, True),
    ('preserve-running', True, False),
])
def test_original_policy_snapshot_gate_and_shutdown_contract(prepared, operation, strategy, choice, mark):
    preparation.test_freeze_before_gate_and_worker_shutdown_before_supervisor(
        prepared, operation, strategy, choice, mark)
