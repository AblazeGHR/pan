"""Use the existing isolated production server with only two owned Sessions."""
import server as fixture

def seed():
    fixture.SESSION_DIR.mkdir(parents=True, exist_ok=True)
    fixture._write_fixture_files()
    fixture.session_store._cache.clear()
    fixture.session_store._all_loaded = False
    for name in ('Chat Stream', 'Alpha Session'):
        fixture.session_store.create(name, adapter='cbc', workdir=str(fixture.WORKDIR))

fixture._seed_sessions = seed
fixture.main()
