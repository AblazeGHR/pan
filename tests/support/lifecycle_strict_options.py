"""Explicit opt-in to the original Windows desktop disappearance assertion."""

def pytest_addoption(parser):
    parser.addoption("--cpx1-require-hidden-desktop", action="store_true", default=False)
