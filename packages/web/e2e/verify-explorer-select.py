"""Native Windows check: Explorer actually selects the requested file.

Uses synthetic files under the system temp directory (spaces, Chinese and commas
in both the directory and file names), calls the real platform boundary, and
reads the resulting selection through Shell.Application. Only windows whose
folder is the synthetic directory are closed.

Requires Windows and pywin32; this is a manual evidence helper, not a unit test:

    python packages/web/e2e/verify-explorer-select.py evidence/.../explorer-select-native.json
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

import win32com.client

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

from packages.core.platform.file_manager import reveal_file_in_file_manager

POLL_SECONDS = 8.0


def explorer_windows(shell):
    return list(shell.Windows())


def window_folder(window) -> Path | None:
    try:
        return Path(window.Document.Folder.Self.Path).resolve()
    except Exception:
        return None


def selected_paths(window) -> list[Path]:
    try:
        return [Path(item.Path).resolve() for item in window.Document.SelectedItems()]
    except Exception:
        return []


def find_selection(shell, folder: Path, target: Path):
    for window in explorer_windows(shell):
        if window_folder(window) != folder:
            continue
        if target in selected_paths(window):
            return window
    return None


def main(output_path: Path) -> int:
    runtime = Path(tempfile.mkdtemp(prefix="pan-select-"))
    folder = runtime / "目录 space,中文"
    folder.mkdir()
    names = [
        "普通 空格.txt",
        "带,逗号 中文.txt",
        "plain.txt",
    ]
    for name in names:
        (folder / name).write_text("synthetic selection fixture\n", encoding="utf-8")

    shell = win32com.client.Dispatch("Shell.Application")
    results = []
    try:
        for name in names:
            target = (folder / name).resolve()
            reveal_file_in_file_manager(target)
            window = None
            for _ in range(int(POLL_SECONDS / 0.2)):
                window = find_selection(shell, folder, target)
                if window is not None:
                    break
                time.sleep(0.2)
            record = {
                "file": name,
                "target": str(target),
                "selected": [str(path) for path in (selected_paths(window) if window else [])],
                "matched": window is not None,
                "windowClosed": False,
            }
            if window is not None:
                try:
                    window.Quit()
                    record["windowClosed"] = True
                except Exception as exc:  # pragma: no cover - manual evidence only
                    record["closeError"] = f"{type(exc).__name__}: {exc}"
                time.sleep(0.3)
            results.append(record)
    finally:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(
                {
                    "platform": sys.platform,
                    "folder": str(folder),
                    "results": results,
                    "pass": all(record["matched"] for record in results),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        shutil.rmtree(runtime, ignore_errors=True)

    passed = all(record["matched"] for record in results)
    print(
        ("PASS" if passed else "FAIL")
        + f" native Explorer selection: {sum(record['matched'] for record in results)}/{len(results)}"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "explorer-select-native.json")))
