"""Allow `python -m packages.remote` as an alias for main.py."""

from .main import main

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
