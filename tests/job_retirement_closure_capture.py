"""Opt-in P23 rerun artifact destination; leaves historical evidence untouched."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def capture_owned_artifacts(monkeypatch):
    destination = ROOT / 'evidence/job-retirement-closure/p23-rerun'
    destination.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(destination)
