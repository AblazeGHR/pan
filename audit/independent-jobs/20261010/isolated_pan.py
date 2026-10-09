"""Real Pan app/lifespan, isolated paths and a paused acceptance Session."""
import argparse
import ctypes
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--job-endpoint", required=True)
    parser.add_argument("--session-id", required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    from packages.core import config
    config.CONFIG_FILE = root / "config.json"
    config.CONFIG_FILE.write_text(json.dumps({
        "plugin_manifests": [], "qq": {"channel": ""},
        "independent_jobs": {"enabled": True, "endpoint": args.job_endpoint},
        "terminals": {"enabled": False},
    }), encoding="utf-8")
    os.environ["PAN_BACKGROUND_JOBS_DIR"] = str(root / "local-jobs")
    os.environ["PAN_SCHEDULER_DIR"] = str(root / "local-jobs")
    os.environ["PAN_TERMINALS_DIR"] = str(root / "terminals")
    from packages.core import session, background_jobs
    from packages.scheduler import store
    session.SESSION_DIR = root / "sessions"
    background_jobs.DEFAULT_ROOT = root / "local-jobs"
    store.DEFAULT_ROOT = root / "local-jobs"
    store.LEGACY_TASKS_ROOT = root / "legacy-scheduler"
    existing = session.get(args.session_id)
    if existing is None:
        existing = session.Session(id=args.session_id, name="job-host-acceptance",
                                   workdir=str(root), queue_paused=True)
        session.save(existing)
    assert existing.queue_paused, "acceptance must not hand off to a provider"
    from packages.web import server
    server.DATA_DIR = root / "data"
    server.WORKDIRS_DIR = root / "data" / "workdirs"
    server.ATTACHMENTS_DIR = root / "data" / "attachments"
    from packages.core.terminal.identity import read_creation_filetime
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    identity = {"pid": os.getpid(), "filetime": str(read_creation_filetime(kernel.GetCurrentProcess()))}
    (root / "pan-identity.json").write_text(json.dumps(identity), encoding="utf-8")
    import uvicorn
    runtime = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=args.port,
                                           log_level="warning", access_log=False))
    server.app.state.pan_uvicorn_server = runtime
    runtime.run()


if __name__ == "__main__":
    main()
