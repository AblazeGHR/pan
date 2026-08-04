"""Tests for Character management system."""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core.character import Character, CharacterManager
from packages.core.manifest_loader import load_manifests, ManifestConfig, Profile


# ------------------------------------------------------------------ #
#  Fixtures
# ------------------------------------------------------------------ #

def _make_temp_data_dir():
    """Create a temporary data/ directory structure."""
    tmp = tempfile.mkdtemp(prefix="pan_char_test_")
    data_dir = Path(tmp) / "data"
    (data_dir / "characters").mkdir(parents=True)
    (data_dir / "memory").mkdir(parents=True)
    return tmp, data_dir


def _write_manifest(base_dir: str, profiles: list[dict] | None = None):
    """Write a minimal manifest.json for testing."""
    if profiles is None:
        profiles = [
            {
                "name": "test-profile",
                "adapter": "cbc",
                "model": "deepseek-v4-flash",
                "system_prompt": "You are a test assistant.",
                "permission_mode": "bypassPermissions",
            }
        ]
    data = {"profiles": profiles, "mcp_servers": [], "command_routes": []}
    path = Path(base_dir) / "manifest.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


# ------------------------------------------------------------------ #
#  Character dataclass
# ------------------------------------------------------------------ #

class TestCharacter:
    def test_roundtrip(self):
        c = Character(
            id="char_test1234abcd",
            profile_name="test-profile",
            name="测试角色",
            adapter="cbc",
            model="deepseek-v4-flash",
            system_prompt="You are helpful.",
            memory_db_path="data/memory/char_test1234abcd.sqlite",
            memory_dir="characters/test/memory",
            created_at="2026-07-29T12:00:00",
        )
        d = c.to_dict()
        c2 = Character.from_dict(d)
        assert c2.id == c.id
        assert c2.profile_name == c.profile_name
        assert c2.name == c.name
        assert c2.system_prompt == c.system_prompt
        assert c2.memory_db_path == c.memory_db_path

    def test_defaults(self):
        c = Character(id="char_x", profile_name="p", name="n")
        assert c.adapter == "cbc"
        assert c.model is None
        assert c.system_prompt == ""
        assert c.created_at == ""  # populated by CharacterManager, not dataclass


# ------------------------------------------------------------------ #
#  CharacterManager
# ------------------------------------------------------------------ #

class TestCharacterManager:
    def test_init_creates_dirs(self):
        tmp, data_dir = _make_temp_data_dir()
        try:
            mgr = CharacterManager(str(data_dir))
            assert Path(data_dir / "characters").exists()
            assert Path(data_dir / "memory").exists()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_load_manifest(self):
        tmp, data_dir = _make_temp_data_dir()
        try:
            _write_manifest(tmp)
            mgr = CharacterManager(str(data_dir))
            cfg = mgr.load_manifest([str(Path(tmp) / "manifest.json")])
            assert len(cfg.profiles) == 1
            assert cfg.profiles[0].name == "test-profile"
            assert "test assistant" in cfg.profiles[0].system_prompt
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_list_profiles_empty_before_load(self):
        tmp, data_dir = _make_temp_data_dir()
        try:
            mgr = CharacterManager(str(data_dir))
            assert mgr.list_profiles() == []
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_list_profiles_after_load(self):
        tmp, data_dir = _make_temp_data_dir()
        try:
            _write_manifest(tmp, profiles=[
                {"name": "a", "system_prompt": "A"},
                {"name": "b", "system_prompt": "B"},
            ])
            mgr = CharacterManager(str(data_dir))
            mgr.load_manifest([str(Path(tmp) / "manifest.json")])
            assert len(mgr.list_profiles()) == 2
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_create_character_from_profile(self):
        tmp, data_dir = _make_temp_data_dir()
        try:
            _write_manifest(tmp)
            mgr = CharacterManager(str(data_dir))
            mgr.load_manifest([str(Path(tmp) / "manifest.json")])

            char = mgr.create_character("test-profile", name="测试", auto_index=False)
            assert char.id.startswith("char_")
            assert char.profile_name == "test-profile"
            assert char.name == "测试"
            assert char.adapter == "cbc"
            assert char.system_prompt == "You are a test assistant."
            assert "char_" in char.memory_db_path
            assert char.created_at != ""

            # Check JSON was persisted
            json_path = Path(data_dir) / "characters" / f"{char.id}.json"
            assert json_path.exists()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_create_character_invalid_profile(self):
        tmp, data_dir = _make_temp_data_dir()
        try:
            mgr = CharacterManager(str(data_dir))
            try:
                mgr.create_character("nonexistent")
                assert False, "Should have raised ValueError"
            except ValueError:
                pass
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_get_character(self):
        tmp, data_dir = _make_temp_data_dir()
        try:
            _write_manifest(tmp)
            mgr = CharacterManager(str(data_dir))
            mgr.load_manifest([str(Path(tmp) / "manifest.json")])
            char = mgr.create_character("test-profile", auto_index=False)

            found = mgr.get_character(char.id)
            assert found is not None
            assert found.id == char.id
            assert found.name == char.name

            assert mgr.get_character("nonexistent") is None
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_list_characters(self):
        tmp, data_dir = _make_temp_data_dir()
        try:
            _write_manifest(tmp, profiles=[
                {"name": "a", "system_prompt": "A"},
                {"name": "b", "system_prompt": "B"},
            ])
            mgr = CharacterManager(str(data_dir))
            mgr.load_manifest([str(Path(tmp) / "manifest.json")])

            mgr.create_character("a", name="一", auto_index=False)
            mgr.create_character("b", name="二", auto_index=False)

            chars = mgr.list_characters()
            assert len(chars) == 2
            names = {c.name for c in chars}
            assert names == {"一", "二"}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_delete_character(self):
        tmp, data_dir = _make_temp_data_dir()
        try:
            _write_manifest(tmp)
            mgr = CharacterManager(str(data_dir))
            mgr.load_manifest([str(Path(tmp) / "manifest.json")])
            char = mgr.create_character("test-profile", auto_index=False)

            char_id = char.id
            assert mgr.delete_character(char_id) is True
            assert mgr.get_character(char_id) is None
            assert mgr.delete_character(char_id) is False  # Already gone
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_create_character_auto_index_no_memory_dir(self):
        """auto_index=True with no memory_dir should not crash."""
        tmp, data_dir = _make_temp_data_dir()
        try:
            _write_manifest(tmp, profiles=[
                {"name": "no-mem", "system_prompt": "test", "memory_dir": None}
            ])
            mgr = CharacterManager(str(data_dir))
            mgr.load_manifest([str(Path(tmp) / "manifest.json")])
            char = mgr.create_character("no-mem", auto_index=True)
            assert char.id.startswith("char_")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_get_memory_manager_no_api_key(self):
        """get_memory_manager without API key is graceful and cached."""
        tmp, data_dir = _make_temp_data_dir()
        try:
            _write_manifest(tmp)
            mgr = CharacterManager(str(data_dir))
            mgr.load_manifest([str(Path(tmp) / "manifest.json")])
            char = mgr.create_character("test-profile", auto_index=False)

            # Must not raise. With the ST provider a manager is constructible
            # without any API key; if so it must be cached (same instance on
            # repeat lookup, #32) and closeable. If None, lookups must agree.
            mm1 = mgr.get_memory_manager(char.id)
            mm2 = mgr.get_memory_manager(char.id)
            if mm1 is not None:
                assert mm2 is mm1, "get_memory_manager not cached"
                mm1.close()
            else:
                assert mm2 is None, "cache and fresh lookup disagree"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
