"""Diagnostic only: force one capability decision, never host platform identity."""
import pytest

from packages.core import platform


@pytest.fixture(autouse=True)
def lifecycle_unsupported_decision(monkeypatch):
    actual = platform.supports
    monkeypatch.setattr(platform, "supports", lambda name:
                        False if name == "service.lifecycle" else actual(name))
