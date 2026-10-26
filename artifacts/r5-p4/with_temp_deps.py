"""Optional collection dependencies, explicitly located outside the profile."""
import sys
if len(sys.argv) < 3 or sys.argv[1] != "--deps":
    raise SystemExit("usage: with_temp_deps.py --deps TEMP_TEST_DEPENDENCIES PYTEST_ARGS...")
sys.path.insert(0, sys.argv[2])
del sys.argv[1:3]
if sys.argv[1:] == ["--import-server"]:
    import packages.web.server
    print("packages.web.server import: OK (Windows, no service startup)")
else:
    import pytest
    sys.exit(pytest.main(sys.argv[1:]))
