"""Host-owned plugin data paths. This is path fencing, not an OS sandbox."""
from pathlib import Path
import re
import json


def plugin_data_dir(data_root, plugin_id, version=1):
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,47}", plugin_id):
        raise ValueError("Invalid plugin data owner")
    base = Path(data_root).resolve()
    root = (base / "plugins").resolve()
    if root.parent != base:
        raise ValueError("Plugin data root escapes the Pan data directory")
    target = (root / plugin_id).resolve()
    if target.parent != root:
        raise ValueError("Plugin data path escapes its owner")
    target.mkdir(parents=True, exist_ok=True)
    metadata = target / '.pan-plugin.json'
    expected = {'pluginId': plugin_id, 'dataVersion': version}
    if metadata.exists():
        if json.loads(metadata.read_text(encoding='utf-8')) != expected:
            raise ValueError('Plugin data version/owner mismatch; explicit migration is required')
    else:
        try:
            with metadata.open('x', encoding='utf-8') as stream:
                json.dump(expected, stream)
        except FileExistsError:
            if json.loads(metadata.read_text(encoding='utf-8')) != expected:
                raise ValueError('Plugin data version/owner mismatch')
    return target
