"""Character management — dataclass, persistence, and memory integration.

Usage::

    from packages.core.character import Character, CharacterManager

    mgr = CharacterManager("data")
    mgr.load_manifest(["D:/project/RuleWhisper/pan_plugin"])

    char = mgr.create_character("coc-keeper", name="我的COC跑团")
    results = mgr.search_memory(char.id, "如何创建角色")
"""

from __future__ import annotations

import json
import hashlib
import logging
import os
import secrets
import stat
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from .memory.embedder import PROVIDER_SENTENCE_TRANSFORMERS

if TYPE_CHECKING:
    from .manifest_loader import ManifestConfig, SessionTemplate, CharacterTemplate
    from .memory import MemoryManager
    from .memory.search import SearchResult

log = logging.getLogger(__name__)


@contextmanager
def _manifest_process_lock(paths: list[Path]):
    """Serialize manifest writes across Pan processes using the same catalog."""
    key = "\n".join(sorted(os.path.normcase(str(path.resolve())) for path in paths))
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    lock_dir = Path(tempfile.gettempdir()) / "pan-manifest-template-locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{digest}.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
        os.lseek(fd, 0, os.SEEK_SET)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            os.lseek(fd, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _file_signature(path: Path) -> tuple[int, int, int, int]:
    info = path.stat()
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def _write_bytes_atomic(path: Path, content: bytes, mode: int) -> None:
    """Replace one file atomically, cleaning up the temporary on every path."""
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp_path, stat.S_IMODE(mode))
        os.replace(temp_path, path)
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            # Directory fsync is unsupported on some platforms (notably
            # Windows); the file contents were still flushed before replace.
            pass
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


# ------------------------------------------------------------------ #
#  Character dataclass
# ------------------------------------------------------------------ #

@dataclass
class Character:
    """A user-created persistent entity owning memory + assets.

    A character is a long-lived identity shared across sessions. It carries no
    session config (system_prompt / adapter / model / mcp_mode / mcp_servers) —
    that comes from a session_template. It only owns retrievable memory and
    (future) assets.
    """

    id: str  # "char_<16 hex chars>"
    name: str  # user-visible name (e.g. "我的COC跑团")
    memory_db_path: str = ""  # e.g. "data/memory/char_abc123.sqlite"
    memory_dir: str | None = None  # directory of .md knowledge files
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "memory_db_path": self.memory_db_path,
            "memory_dir": self.memory_dir,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Character:
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            memory_db_path=data.get("memory_db_path", ""),
            memory_dir=data.get("memory_dir"),
            created_at=data.get("created_at", ""),
        )


# ------------------------------------------------------------------ #
#  CharacterManager
# ------------------------------------------------------------------ #

class CharacterManager:
    """Manages character CRUD, persistence, and memory integration."""

    def __init__(self, data_dir: str = "data") -> None:
        self._data_dir = Path(data_dir)
        self._characters_dir = self._data_dir / "characters"
        self._memory_dir = self._data_dir / "memory"
        self._characters_dir.mkdir(parents=True, exist_ok=True)
        self._memory_dir.mkdir(parents=True, exist_ok=True)

        self._memory_managers: dict[str, MemoryManager] = {}
        self._memory_managers_lock = threading.Lock()  # guards the dict (#32)
        self._manifest_config: ManifestConfig | None = None
        # Saves and reloads share this lock so validation, disk replacement,
        # and catalog refresh observe one in-process manifest generation.
        self._manifest_write_lock = threading.RLock()

        # Hot-reload bookkeeping. ``_plugin_paths`` is the same list passed to
        # ``load_manifest`` so ``reload_manifest`` can re-read the exact same
        # files. The mtime snapshot + file count let us do a cheap stat-only
        # change check (no file read / parse) on the hot path.
        self._plugin_paths: list[str] = []
        self._manifest_mtime_snapshot: float = 0.0
        self._manifest_file_count: int = 0

        # Throttle for the cheap stat-only change check: avoid re-statting the
        # manifest files on every request in a high-frequency burst. The stat
        # result is cached for ``_manifest_check_ttl`` seconds. A forced reload
        # via ``reload_manifest()`` (e.g. POST /api/manifest/reload) ALWAYS
        # re-reads and is NOT subject to this window, so a deterministic
        # refresh path always exists even within the throttle window.
        self._manifest_check_ttl: float = 1.0
        self._cached_mtime: float = 0.0
        self._cached_count: int = 0
        self._cached_check_ts: float = 0.0

    # ------------------------------------------------------------------ #
    #  Manifest
    # ------------------------------------------------------------------ #

    def load_manifest(self, plugin_paths: list[str]) -> ManifestConfig:
        from .manifest_loader import load_manifests

        with self._manifest_write_lock:
            self._plugin_paths = list(plugin_paths)
            self._manifest_config = load_manifests(plugin_paths)
            self._refresh_manifest_state()
            return self._manifest_config

    # --- manifest hot-reload ------------------------------------------- #

    def _manifest_state(self) -> tuple[float, int]:
        """Return ``(max_mtime, file_count)`` for the resolved manifest files.

        Stat-only — does NOT read or parse any file. ``file_count`` lets us
        detect additions/removals (a deleted newest file would otherwise lower
        the max mtime and look "unchanged").
        """
        from .manifest_loader import resolve_manifest_files

        if not self._plugin_paths:
            return 0.0, 0
        mtime = 0.0
        files = resolve_manifest_files(self._plugin_paths)
        for mf in files:
            try:
                mtime = max(mtime, mf.stat().st_mtime)
            except OSError:
                pass
        return mtime, len(files)

    def _refresh_manifest_state(self) -> None:
        """Cache the current mtime + file count after a (re)load."""
        self._manifest_mtime_snapshot, self._manifest_file_count = (
            self._manifest_state()
        )
        # Invalidate the throttle cache so the next change-check re-stats
        # (the file set / mtime just changed under our feet).
        self._cached_check_ts = 0.0

    def manifest_changed(self) -> bool:
        """True if any resolved manifest file changed since the last load.

        Cheap: only ``stat``s files (no read/parse). True when the newest mtime
        advanced OR the set of resolved files changed (add/remove a manifest).

        Throttled: within ``_manifest_check_ttl`` seconds of the previous
        check we reuse the last stat result instead of re-statting. This is
        purely an optimisation — ``reload_manifest()`` (the manual /
        deterministic refresh) never goes through this window.
        """
        now = time.monotonic()
        if self._cached_check_ts and (now - self._cached_check_ts) < self._manifest_check_ttl:
            mtime, count = self._cached_mtime, self._cached_count
        else:
            mtime, count = self._manifest_state()
            self._cached_mtime, self._cached_count, self._cached_check_ts = (
                mtime, count, now,
            )
        return (
            mtime > self._manifest_mtime_snapshot
            or count != self._manifest_file_count
        )

    def _manifest_files_parse_ok(
        self, plugin_paths: list[str] | None = None
    ) -> tuple[bool, list[str]]:
        """``(ok, errors)`` — whether every resolved manifest currently parses.

        Used by ``reload_manifest`` to abort and keep the old config when a
        manifest is broken (instead of swapping in a partial/silent result).
        Pass *plugin_paths* to validate a candidate list that is not loaded
        yet (``reload_plugin_paths``); defaults to the current paths.
        """
        from .manifest_loader import resolve_manifest_files

        paths = self._plugin_paths if plugin_paths is None else plugin_paths
        errors: list[str] = []
        if not paths:
            return True, errors
        for mf in resolve_manifest_files(paths):
            try:
                json.loads(mf.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                errors.append(f"{mf}: {exc}")
        return (len(errors) == 0), errors

    def reload_manifest(self) -> ManifestConfig | None:
        """Hot-reload the same ``plugin_paths``, atomically replacing config.

        Re-reads + re-parses the manifest files and replaces the whole
        ``_manifest_config`` in one assignment so every consumer (session
        templates, mcp_servers, command_routes, character templates) stays
        consistent. On failure (unreadable / unparseable manifest) the previous
        config is preserved and the error logged — callers never see a crash or
        a partial config.

        Returns the (possibly unchanged) config.
        """
        with self._manifest_write_lock:
            return self._reload_manifest_unlocked()

    def _reload_manifest_unlocked(self) -> ManifestConfig | None:
        if not self._plugin_paths:
            # Nothing was ever loaded via paths; nothing to reload.
            return self._manifest_config

        ok, errors = self._manifest_files_parse_ok()
        if not ok:
            log.error(
                "Manifest reload aborted — %d file(s) failed to parse; "
                "keeping previous config: %s",
                len(errors), errors,
            )
            return self._manifest_config

        from .manifest_loader import load_manifests

        try:
            new_config = load_manifests(self._plugin_paths)
        except Exception:
            log.exception("Manifest reload failed; keeping previous config")
            return self._manifest_config

        # Atomic swap: replace the whole config object at once.
        self._manifest_config = new_config
        self._refresh_manifest_state()
        return self._manifest_config

    def reload_plugin_paths(
        self, plugin_paths: list[str]
    ) -> tuple[ManifestConfig | None, list[str]]:
        """Swap in a NEW ``plugin_paths`` list and load manifests from it.

        Unlike ``reload_manifest`` (which re-reads the same already-registered
        files), this applies a changed ``plugin_manifests`` list read from
        config.json — manifest files added to or removed from the list take
        effect without a restart. Failure-preserving: if any manifest in the
        new list fails to parse, or loading raises, the previous paths AND
        config are kept intact (never a partial swap).

        Returns ``(config, errors)``: on success ``(new_config, [])``; on
        abort ``(None, errors)`` with the previous state untouched.
        """
        with self._manifest_write_lock:
            return self._reload_plugin_paths_unlocked(plugin_paths)

    def _reload_plugin_paths_unlocked(
        self, plugin_paths: list[str]
    ) -> tuple[ManifestConfig | None, list[str]]:
        ok, errors = self._manifest_files_parse_ok(list(plugin_paths))
        if not ok:
            log.error(
                "Plugin paths reload aborted — %d file(s) failed to parse; "
                "keeping previous config: %s",
                len(errors), errors,
            )
            return None, errors

        from .manifest_loader import load_manifests

        try:
            new_config = load_manifests(list(plugin_paths))
        except Exception:
            log.exception("Plugin paths reload failed; keeping previous config")
            return None, ["manifest load failed (see server log)"]

        # Atomic swap: replace paths + config together, then refresh the
        # mtime snapshot so change detection tracks the new file set.
        self._plugin_paths = list(plugin_paths)
        self._manifest_config = new_config
        self._refresh_manifest_state()
        return new_config, []

    @staticmethod
    def _manifest_target_id(path: Path) -> str:
        identity = os.path.normcase(str(path.resolve()))
        return "manifest-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()

    @staticmethod
    def _manifest_target_label(path: Path) -> str:
        from .manifest_loader import REPO_ROOT

        resolved = path.resolve()
        try:
            return resolved.relative_to(REPO_ROOT.resolve()).as_posix()
        except ValueError:
            return f"{resolved.parent.name}/manifest.json"

    @staticmethod
    def _manifest_write_status(path: Path) -> tuple[bool, str | None]:
        try:
            info = path.stat()
            parent_info = path.parent.stat()
            has_mode_write = bool(info.st_mode & 0o222) and bool(parent_info.st_mode & 0o222)
            if not has_mode_write or not os.access(path, os.W_OK) or not os.access(path.parent, os.W_OK):
                return False, "Manifest file or containing directory is not writable"
        except OSError:
            return False, "Manifest file or containing directory is not accessible"
        return True, None

    def list_session_template_manifest_targets(self) -> list[dict]:
        """List parseable manifests from the exact paths loaded by this manager.

        Only opaque stable IDs leave the server. A client can select a loaded
        target, but cannot submit a filesystem path to the save API.
        """
        from .manifest_loader import resolve_manifest_files

        with self._manifest_write_lock:
            targets: list[dict] = []
            for path in resolve_manifest_files(self._plugin_paths):
                try:
                    data = json.loads(path.read_text(encoding="utf-8-sig"))
                except (OSError, UnicodeError, json.JSONDecodeError):
                    continue
                if not isinstance(data, dict):
                    continue
                writable, reason = self._manifest_write_status(path)
                targets.append({
                    "id": self._manifest_target_id(path),
                    "label": self._manifest_target_label(path),
                    "writable": writable,
                    "reason": reason,
                })
            return targets

    @staticmethod
    def _read_manifest_snapshot(path: Path) -> dict:
        before = _file_signature(path)
        content = path.read_bytes()
        after = _file_signature(path)
        if before != after:
            raise ValueError("A loaded manifest changed while it was being read; retry the save")
        try:
            data = json.loads(content.decode("utf-8-sig"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("All loaded manifests must be readable valid JSON before saving") from exc
        if not isinstance(data, dict):
            raise ValueError("All loaded manifests must contain a JSON object")
        return {
            "path": path,
            "content": content,
            "signature": after,
            "mode": path.stat().st_mode,
            "data": data,
        }

    def save_session_template(
        self,
        target_id: str,
        payload: dict,
        *,
        validate: Callable[[dict], dict],
    ) -> dict:
        """Create and atomically append one validated template to a loaded manifest."""
        from .manifest_loader import resolve_manifest_files

        if not isinstance(target_id, str) or not target_id:
            raise ValueError("manifestId must be a loaded manifest target id")
        with self._manifest_write_lock:
            paths = resolve_manifest_files(self._plugin_paths)
            if not paths:
                raise ValueError("No loaded manifest is available for saving")
            with _manifest_process_lock(paths):
                snapshots = [self._read_manifest_snapshot(path) for path in paths]
                target = next((
                    item for item in snapshots
                    if self._manifest_target_id(item["path"]) == target_id
                ), None)
                if target is None:
                    raise ValueError("manifestId is not one of the currently loaded manifest targets")
                writable, reason = self._manifest_write_status(target["path"])
                if not writable:
                    raise ValueError(reason or "Manifest is not writable")

                # Validate under the same lock as the global duplicate-name
                # scan and replacement, against the current loaded MCP catalog.
                normalized = validate(payload)
                name = normalized["name"]
                for snapshot in snapshots:
                    manifest = snapshot["data"]
                    for key in ("session_templates", "profiles"):
                        entries = manifest.get(key, [])
                        if not isinstance(entries, list):
                            raise ValueError(f"Manifest {key} must be an array before saving")
                        if any(not isinstance(entry, dict) for entry in entries):
                            raise ValueError(f"Manifest {key} entries must be objects before saving")
                        if any(entry.get("name") == name for entry in entries):
                            raise ValueError(f"Session Template {name!r} already exists in a loaded manifest")

                for snapshot in snapshots:
                    try:
                        unchanged = _file_signature(snapshot["path"]) == snapshot["signature"]
                    except OSError:
                        unchanged = False
                    if not unchanged:
                        raise ValueError("A loaded manifest changed while saving; reload and retry")

                manifest = target["data"]
                templates = manifest.setdefault("session_templates", [])
                if not isinstance(templates, list):
                    raise ValueError("Target manifest session_templates must be an array")
                templates.append(normalized)
                written = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
                path = target["path"]
                _write_bytes_atomic(path, written, target["mode"])

                try:
                    refreshed = self._reload_manifest_unlocked()
                    saved = refreshed.get_session_template(name) if refreshed is not None else None
                    if saved is None or Path(saved.source_manifest).resolve() != path.parent.resolve():
                        raise RuntimeError("The saved template did not appear in the refreshed manifest catalog")
                except Exception as exc:
                    try:
                        current = path.read_bytes()
                        if current != written:
                            raise RuntimeError("Manifest changed after replacement; safe rollback was not possible")
                        _write_bytes_atomic(path, target["content"], target["mode"])
                        self._reload_manifest_unlocked()
                    except Exception as rollback_exc:
                        raise RuntimeError(
                            f"Manifest refresh failed ({exc}); rollback failed ({rollback_exc})"
                        ) from rollback_exc
                    raise RuntimeError(f"Manifest refresh failed; save was rolled back: {exc}") from exc

                return {
                    "manifestId": target_id,
                    "manifestLabel": self._manifest_target_label(path),
                    "sessionTemplate": normalized,
                }

    def list_session_templates(self) -> list[SessionTemplate]:
        if self._manifest_config is None:
            return []
        return self._manifest_config.session_templates

    def list_character_templates(self) -> list[CharacterTemplate]:
        if self._manifest_config is None:
            return []
        return self._manifest_config.character_templates

    def list_command_routes(self):
        """Return manifest command_routes for QQ Bot prefix routing.

        Returns an empty list if no manifest is loaded — callers (e.g. the QQ
        plugin via ``GET /api/manifest/command-routes``) treat empty as "no
        prefix routing, all messages go to LLM path".
        """
        if self._manifest_config is None:
            return []
        return self._manifest_config.command_routes

    def get_session_template(self, name: str) -> SessionTemplate | None:
        if self._manifest_config is None:
            return None
        return self._manifest_config.get_session_template(name)

    def get_character_template(self, name: str) -> CharacterTemplate | None:
        if self._manifest_config is None:
            return None
        return self._manifest_config.get_character_template(name)

    # ------------------------------------------------------------------ #
    #  Character CRUD
    # ------------------------------------------------------------------ #

    def create_character(
        self,
        template_name: str,
        name: str | None = None,
        auto_index: bool = True,
    ) -> Character:
        template = self.get_character_template(template_name)
        if template is None:
            raise ValueError(f"Character template not found: {template_name}")

        char_id = "char_" + secrets.token_hex(8)

        char = Character(
            id=char_id,
            name=name if name is not None else template.name,
            memory_db_path=str(self._memory_dir / f"{char_id}.sqlite"),
            memory_dir=template.memory_dir,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        if auto_index and char.memory_dir:
            try:
                mgr = self.get_memory_manager(char_id)
                if mgr is not None:
                    mgr.index_directory(char.memory_dir)
            except Exception:
                log.warning(
                    "Memory indexing failed for character %s (non-fatal)",
                    char_id,
                    exc_info=True,
                )

        self._save_character(char)
        return char

    def get_character(self, character_id: str) -> Character | None:
        file_path = self._characters_dir / f"{character_id}.json"
        if not file_path.exists():
            return None
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            return Character.from_dict(data)
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Failed to load character %s: %s", character_id, exc)
            return None

    def list_characters(self) -> list[Character]:
        characters: list[Character] = []
        for f in sorted(self._characters_dir.glob("*.json")):
            char_id = f.stem
            char = self.get_character(char_id)
            if char is not None:
                characters.append(char)
        return characters

    def delete_character(self, character_id: str) -> bool:
        json_path = self._characters_dir / f"{character_id}.json"
        sqlite_path = self._memory_dir / f"{character_id}.sqlite"

        deleted = False
        if json_path.exists():
            json_path.unlink()
            deleted = True

        if sqlite_path.exists():
            sqlite_path.unlink()
            deleted = True

        # Remove from lazy cache (close first if loaded)
        with self._memory_managers_lock:
            mgr = self._memory_managers.pop(character_id, None)
        if mgr is not None:
            try:
                mgr.close()
            except Exception:
                log.warning(
                    "Error closing MemoryManager for %s during delete",
                    character_id,
                    exc_info=True,
                )

        return deleted

    # ------------------------------------------------------------------ #
    #  Memory integration
    # ------------------------------------------------------------------ #

    def get_memory_manager(
        self, character_id: str, api_key: str | None = None
    ) -> MemoryManager | None:
        with self._memory_managers_lock:
            if character_id in self._memory_managers:
                return self._memory_managers[character_id]

        char = self.get_character(character_id)
        if char is None:
            return None

        from .memory import MemoryManager

        try:
            mgr = MemoryManager(
                db_path=char.memory_db_path,
                api_key=api_key,
                provider=PROVIDER_SENTENCE_TRANSFORMERS,
            )
        except Exception:
            log.warning(
                "Failed to create MemoryManager for character %s (no API key or local model?)",
                character_id,
                exc_info=True,
            )
            return None

        # Re-check under the lock: another thread may have created the manager
        # (or deleted the character) while we were building it (#32).
        with self._memory_managers_lock:
            existing = self._memory_managers.get(character_id)
            if existing is not None:
                try:
                    mgr.close()
                except Exception:
                    pass
                return existing
            self._memory_managers[character_id] = mgr
        return mgr

    def search_memory(
        self,
        character_id: str,
        query: str,
        max_results: int = 3,
    ) -> list[SearchResult]:
        mgr = self.get_memory_manager(character_id)
        if mgr is None:
            return []
        try:
            return mgr.search(query, max_results=max_results)
        except Exception:
            log.warning(
                "Memory search failed for character %s",
                character_id,
                exc_info=True,
            )
            return []

    def inject_context(
        self, character_id: str, task_text: str
    ) -> str:
        results = self.search_memory(character_id, task_text)
        if not results:
            return task_text

        context_parts = []
        for i, r in enumerate(results, 1):
            context_parts.append(
                f"[{i}] ({r.path}:{r.start_line}-{r.end_line})\n{r.text}"
            )

        header = (
            "以下是与当前任务相关的记忆上下文（仅供参考）：\n"
            "---\n"
        )
        body = "\n\n".join(context_parts)
        footer = "\n---\n"

        return header + body + footer + task_text

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    def _save_character(self, character: Character) -> None:
        file_path = self._characters_dir / f"{character.id}.json"
        data = json.dumps(
            character.to_dict(), ensure_ascii=False, indent=2
        )
        file_path.write_text(data, encoding="utf-8")
