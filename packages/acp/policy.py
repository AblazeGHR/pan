"""Fail-closed ACP profile parsing and workspace checks.

Only server-owned configuration is an authority. A cwd or session identifier
from a client is never itself a grant. No files are created by this module.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

NAME = re.compile(r'[A-Za-z0-9_-]{1,64}\Z')


@dataclass(frozen=True)
class Profile:
    name: str
    template: str
    roots: tuple[Path, ...]


def validate(name, raw):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError('invalid ACP profile')
    if not isinstance(raw, dict) or set(raw) != {'enabled', 'template', 'workspaceRoots'}:
        raise ValueError('invalid ACP profile')
    if raw['enabled'] is not True or not isinstance(raw['template'], str) or not raw['template'].strip():
        raise ValueError('ACP profile disabled or missing template')
    paths = raw['workspaceRoots']
    if not isinstance(paths, list) or not 1 <= len(paths) <= 32:
        raise ValueError('invalid workspace roots')
    roots = []
    for item in paths:
        if not isinstance(item, str) or not os.path.isabs(item):
            raise ValueError('absolute workspace roots required')
        root = Path(item).resolve(strict=True)
        if not root.is_dir():
            raise ValueError('workspace root is not a directory')
        roots.append(root)
    return Profile(name, raw['template'], tuple(roots))


def resolve_workspace(profile, cwd):
    if not isinstance(profile, Profile) or not isinstance(cwd, str) or not os.path.isabs(cwd):
        raise ValueError('invalid workspace')
    path = Path(cwd).resolve(strict=True)
    if not path.is_dir() or not any(path == root or path.is_relative_to(root) for root in profile.roots):
        raise ValueError('workspace outside profile')
    return path
