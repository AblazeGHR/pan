"""Absolute-path entry point used by registered pan.cmd (independent of cwd)."""
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from packages.core.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
