"""Fail-closed process preflight for the disposable Windows Zotero fixture."""


def require_no_existing_zotero(processes, *, owned_pids=()):
    owned = set(owned_pids)
    existing = []
    for process in processes:
        try:
            name = process.name()
        except Exception as exc:
            raise RuntimeError('cannot establish Zotero process inventory') from exc
        if name.lower() in ('zotero.exe', 'zotero') and process.pid not in owned:
            existing.append(process.pid)
    if existing:
        raise RuntimeError('existing Zotero process detected; isolated launch refused')
