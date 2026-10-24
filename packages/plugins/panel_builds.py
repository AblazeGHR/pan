"""Host-owned build store for ``web-app/v1`` plugin panels.

A panel build runs the argv declared by the plugin manifest inside the plugin
directory and copies the declared output into a Pan-owned directory. Serving
reads only that copy, so a rebuild cannot expose the rest of the disk, and a
failed build never replaces the last usable artifact.

This is path fencing for a local trusted build command, not a sandbox.
"""
from __future__ import annotations

import asyncio
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import time
import uuid
from types import SimpleNamespace

STATE_PROTOCOL = "pan-panel-state/v1"
BUILD_PROTOCOL = "pan-panel-build/v1"
DEFAULT_TIMEOUT = 300
LOG_LIMIT = 200_000
MAX_BUILD_FILES = 20_000
MAX_BUILD_BYTES = 512 * 1024 * 1024
_SAFE_ID = re.compile(r"[a-z][a-z0-9_-]{0,47}\Z")
_MEDIA = {
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".cjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".json": "application/json",
    ".map": "application/json",
    ".svg": "image/svg+xml",
    ".wasm": "application/wasm",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}


class PanelNotReady(ValueError):
    """The panel has no enabled, successful build to serve."""


def _blank(plugin_id: str, panel_id: str) -> dict:
    return {"protocol": STATE_PROTOCOL, "pluginId": plugin_id, "panelId": panel_id,
            "enabled": False, "activeBuild": None, "status": "unbuilt",
            "lastError": None, "startedAt": None, "finishedAt": None,
            "builtAt": None, "log": ""}


def _milliseconds(value):
    return None if value is None else int(value * 1000)


def _entry_parts(entry: str) -> tuple[str, ...]:
    if not isinstance(entry, str) or not entry:
        raise ValueError("Panel entry is missing")
    path = PurePosixPath(entry.replace("\\", "/"))
    if path.is_absolute() or not path.parts or any(
            part in {"", ".", ".."} or ":" in part for part in path.parts):
        raise ValueError("Panel entry is not a relative path inside the build output")
    return path.parts


def normalize_asset_path(raw: str) -> list[str]:
    from urllib.parse import unquote
    text = unquote(raw or "").replace("\\", "/")
    if "\x00" in text:
        raise ValueError("Invalid panel asset path")
    parts = [part for part in text.strip("/").split("/") if part]
    if any(part in {".", ".."} or ":" in part for part in parts):
        raise ValueError("Invalid panel asset path")
    return parts


def media_type(path: Path) -> str:
    return _MEDIA.get(path.suffix.lower(), mimetypes.guess_type(path.name)[0] or "application/octet-stream")


class PanelBuildStore:
    """Persistent panel enable/build state plus immutable served artifacts."""

    def __init__(self, data_root):
        self.root = Path(data_root).resolve() / "plugin-panels"
        self._states: dict[tuple[str, str], dict] = {}
        self._tasks: dict[tuple[str, str], asyncio.Task] = {}
        self._procs: set = set()
        self._guard = asyncio.Lock()

    # ── paths ──
    def _owned(self, plugin_id: str, panel_id: str) -> Path:
        if not _SAFE_ID.fullmatch(plugin_id or "") or not _SAFE_ID.fullmatch(panel_id or ""):
            raise ValueError("Invalid plugin panel identity")
        return self.root / plugin_id / panel_id

    def _state_path(self, plugin_id: str, panel_id: str) -> Path:
        return self._owned(plugin_id, panel_id) / "state.json"

    def _builds_root(self, plugin_id: str, panel_id: str) -> Path:
        return self._owned(plugin_id, panel_id) / "builds"

    def _build_dir(self, plugin_id: str, panel_id: str, build_id: str) -> Path:
        if not re.fullmatch(r"[0-9A-Za-z_-]{8,80}", build_id or ""):
            raise ValueError("Invalid panel build identity")
        return self._builds_root(plugin_id, panel_id) / build_id

    # ── state ──
    def _state(self, plugin_id: str, panel_id: str) -> dict:
        key = (plugin_id, panel_id)
        if key in self._states:
            return self._states[key]
        path = self._state_path(plugin_id, panel_id)
        state = _blank(plugin_id, panel_id)
        if path.exists():
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                saved = None
            if isinstance(saved, dict) and saved.get("protocol") == STATE_PROTOCOL \
                    and saved.get("pluginId") == plugin_id and saved.get("panelId") == panel_id:
                state.update({k: saved[k] for k in state if k in saved})
                state["protocol"] = STATE_PROTOCOL
        if state["activeBuild"] and not (self._build_dir(plugin_id, panel_id, state["activeBuild"]) / ".pan-panel-build.json").is_file():
            state.update(activeBuild=None, enabled=False,
                         status="unbuilt" if state["status"] != "failed" else state["status"])
        if state["status"] == "building":
            # A build cannot survive a host restart; report it instead of
            # leaving the GUI waiting forever.
            state.update(status="failed", lastError="Build was interrupted by a Pan restart.",
                         finishedAt=time.time(),
                         log=(state.get("log") or "") + "\nBuild interrupted by a Pan restart.")
            self._save(state)
        self._states[key] = state
        return state

    def _save(self, state: dict) -> None:
        path = self._state_path(state["pluginId"], state["panelId"])
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)

    def status(self, plugin_id: str, panel_id: str) -> dict:
        state = self._state(plugin_id, panel_id)
        return {"pluginId": plugin_id, "panelId": panel_id,
                "enabled": bool(state["enabled"]),
                "status": state["status"],
                "available": bool(state["activeBuild"]),
                "activeBuild": state["activeBuild"],
                "builtAt": _milliseconds(state["builtAt"]),
                "startedAt": _milliseconds(state["startedAt"]),
                "finishedAt": _milliseconds(state["finishedAt"]),
                "lastError": state["lastError"],
                "log": state["log"]}

    def is_enabled(self, plugin_id: str, panel_id: str) -> bool:
        return bool(self._state(plugin_id, panel_id)["enabled"] and self._state(plugin_id, panel_id)["activeBuild"])

    def set_enabled(self, plugin_id: str, panel_id: str, enabled: bool) -> dict:
        state = self._state(plugin_id, panel_id)
        if enabled and not state["activeBuild"]:
            raise ValueError("Panel has no successful build; build it before enabling")
        if enabled and not (self._build_dir(plugin_id, panel_id, state["activeBuild"]) / ".pan-panel-build.json").is_file():
            raise ValueError("Panel build artifact is missing; rebuild it before enabling")
        state["enabled"] = bool(enabled)
        self._save(state)
        return self.status(plugin_id, panel_id)

    def unbuild(self, plugin_id: str, panel_id: str) -> dict:
        running = self._tasks.get((plugin_id, panel_id))
        if running is not None and not running.done():
            raise ValueError("Cannot unbuild while a build is running")
        state = self._state(plugin_id, panel_id)
        builds = self._builds_root(plugin_id, panel_id)
        if builds.exists():
            shutil.rmtree(builds)
        state.update(enabled=False, activeBuild=None, status="unbuilt", lastError=None,
                     startedAt=None, finishedAt=None, builtAt=None, log="")
        self._save(state)
        return self.status(plugin_id, panel_id)

    # ── build ──
    @staticmethod
    def _spec(panel: dict) -> dict:
        build = panel.get("build") or {}
        return {"command": list(build.get("command") or []),
                "cwd": Path(build.get("cwd") or "."),
                "output": Path(build.get("output") or ""),
                "entry": panel.get("entry"),
                "timeout": int(build.get("timeout_seconds", DEFAULT_TIMEOUT))}

    async def start(self, plugin_id: str, panel: dict) -> dict:
        spec = self._spec(panel)
        if not spec["command"]:
            raise ValueError("Panel has no build command")
        key = (plugin_id, panel["id"])
        async with self._guard:
            running = self._tasks.get(key)
            if running is not None and not running.done():
                return self.status(plugin_id, panel["id"])
            state = self._state(plugin_id, panel["id"])
            state.update(status="building", startedAt=time.time(), finishedAt=None,
                         lastError=None, log="")
            self._save(state)
            task = asyncio.create_task(self._run(key, plugin_id, panel["id"], spec),
                                       name=f"panel-build-{plugin_id}-{panel['id']}")
            self._tasks[key] = task
            task.add_done_callback(lambda finished, k=key: self._retire(k, finished))
        return self.status(plugin_id, panel["id"])

    def _retire(self, key, task) -> None:
        if self._tasks.get(key) is task:
            self._tasks.pop(key, None)
        if not task.cancelled():
            task.exception()

    async def wait(self, plugin_id: str, panel_id: str) -> dict:
        task = self._tasks.get((plugin_id, panel_id))
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        return self.status(plugin_id, panel_id)

    async def _run(self, key, plugin_id: str, panel_id: str, spec: dict) -> None:
        state = self._states[key]
        log: list[str] = []
        size = 0

        def append(text: str) -> None:
            nonlocal size
            log.append(text)
            size += len(text)
            while size > LOG_LIMIT and log:
                size -= len(log[0])
                log.pop(0)
            state["log"] = "".join(log)

        failure = None
        code = None
        staging = None
        try:
            if not spec["output"].is_absolute() or not spec["cwd"].is_absolute():
                raise ValueError("Panel build paths must be resolved by the manifest loader")
            if not spec["cwd"].is_dir():
                raise ValueError("Panel build cwd does not exist")
            from packages.jobs.window import command_window_options
            process = await asyncio.create_subprocess_exec(
                *spec["command"], cwd=str(spec["cwd"]),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                env={**os.environ, "PAN_PLUGIN_DIR": str(spec["cwd"])},
                **command_window_options())
            self._procs.add(process)

            async def drain() -> None:
                assert process.stdout is not None
                while True:
                    chunk = await process.stdout.read(4096)
                    if not chunk:
                        break
                    append(chunk.decode("utf-8", "replace"))

            reader = asyncio.create_task(drain())
            try:
                code = await asyncio.wait_for(process.wait(), spec["timeout"])
            except TimeoutError:
                process.kill()
                await process.wait()
                failure = f"Build timed out after {spec['timeout']} seconds."
            finally:
                await asyncio.gather(reader, return_exceptions=True)
                self._procs.discard(process)
            if failure is None and code != 0:
                failure = f"Build command exited with code {code}."
            if failure is None:
                build_id = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
                final = self._build_dir(plugin_id, panel_id, build_id)
                staging = self._builds_root(plugin_id, panel_id) / f".tmp-{build_id}"
                self._copy_output(spec["output"], staging, spec["entry"])
                metadata = {"protocol": BUILD_PROTOCOL, "pluginId": plugin_id,
                            "panelId": panel_id, "entry": spec["entry"],
                            "createdAt": time.time(), "command": spec["command"]}
                (staging / ".pan-panel-build.json").write_text(
                    json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
                final.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staging, final)
                staging = None
                self._prune(plugin_id, panel_id, keep=build_id)
                state.update(activeBuild=build_id, status="built", builtAt=metadata["createdAt"],
                             lastError=None)
        except asyncio.CancelledError:
            failure = "Build was cancelled."
            raise
        except Exception as exc:  # build tooling failure stays in the panel state
            failure = str(exc) or exc.__class__.__name__
        finally:
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)
            if failure is not None and state["status"] == "building":
                state.update(status="failed", lastError=failure)
                append(("\n" if log else "") + failure + "\n")
            state["finishedAt"] = time.time()
            self._save(state)

    def _copy_output(self, source: Path, target: Path, entry: str) -> None:
        parts = _entry_parts(entry)
        root = source.resolve()
        if not root.is_dir():
            raise ValueError("Build output directory is missing")
        target.mkdir(parents=True, exist_ok=True)
        files = 0
        total = 0
        for candidate in root.rglob("*"):
            if candidate.is_symlink():
                continue
            resolved = candidate.resolve()
            if resolved != root and root not in resolved.parents:
                continue
            relative = resolved.relative_to(root)
            destination = target / relative
            if resolved.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            if not resolved.is_file():
                continue
            files += 1
            total += resolved.stat().st_size
            if files > MAX_BUILD_FILES or total > MAX_BUILD_BYTES:
                raise ValueError("Build output is too large to publish")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(resolved, destination)
        if not (target / Path(*parts)).is_file():
            raise ValueError("Build output does not contain the declared entry file")

    def _prune(self, plugin_id: str, panel_id: str, keep: str) -> None:
        root = self._builds_root(plugin_id, panel_id)
        for item in root.iterdir() if root.exists() else []:
            if item.name != keep:
                shutil.rmtree(item, ignore_errors=True)

    # ── serving ──
    def asset(self, plugin_id: str, panel_id: str, raw_path: str):
        state = self._state(plugin_id, panel_id)
        if not state["enabled"]:
            raise PanelNotReady("Panel is disabled")
        if not state["activeBuild"]:
            raise PanelNotReady("Panel has no successful build")
        build = self._build_dir(plugin_id, panel_id, state["activeBuild"])
        metadata = json.loads((build / ".pan-panel-build.json").read_text(encoding="utf-8"))
        entry = Path(*_entry_parts(metadata.get("entry")))
        parts = normalize_asset_path(raw_path)
        if any(part.startswith(".pan-") for part in parts):
            return None  # Host build metadata is not part of the panel site.
        target = build.joinpath(*parts) if parts else build
        if target.is_dir():
            target = target / entry
        elif not target.exists() and parts and "." not in parts[-1]:
            target = build / entry  # SPA route without a file extension
        resolved = target.resolve()
        if resolved != build.resolve() and build.resolve() not in resolved.parents:
            raise ValueError("Panel asset path escapes the build output")
        if not resolved.is_file():
            return None
        # Entry HTML and every asset are revalidated after a rebuild.
        return SimpleNamespace(path=resolved, media_type=media_type(resolved), cache="no-cache")

    async def close(self) -> None:
        for process in list(self._procs):
            try:
                if process.returncode is None:
                    process.kill()
            except ProcessLookupError:
                pass
        self._procs.clear()
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
