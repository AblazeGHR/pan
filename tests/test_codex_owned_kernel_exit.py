"""Owned native kernel exposes subprocess-compatible observed exit facts."""
import subprocess
import pytest
from packages.core.adapters.codex.owned_kernel import OwnedKernel

class Backend:
    def __init__(self, dead, code):
        self.dead, self.code, self.reads = dead, code, 0
    def wait_dead(self, timeout):
        return self.dead
    def exit_code(self):
        self.reads += 1
        return self.code

def kernel(dead, code):
    result = object.__new__(OwnedKernel)
    result.backend = Backend(dead, code)
    return result

@pytest.mark.parametrize('code', [0, 7, 259, None])
def test_confirmed_dead_returns_observed_code_without_method_or_guess(code):
    process = kernel(True, code)
    assert process.poll() == code
    assert process.returncode == code
    assert process.wait(timeout=0) == code
    assert process.backend.reads == 3

def test_unproven_exit_never_reads_or_invents_exit_code():
    process = kernel(False, 7)
    assert process.poll() is None
    assert process.returncode is None
    with pytest.raises(subprocess.TimeoutExpired):
        process.wait(timeout=0)
    assert process.backend.reads == 0
