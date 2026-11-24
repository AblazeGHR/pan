"""Fail-closed ACP profile parsing and workspace checks.

Only server-owned configuration is an authority. A cwd or session identifier
from a client is never itself a grant. No files are created by this module.
"""
from __future__ import annotations

import os
import re
import hashlib
import json
import stat
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname

NAME = re.compile(r'[A-Za-z0-9_-]{1,64}\Z')


def _reject_reparse(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or (getattr(info, 'st_file_attributes', 0)
                                      & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)):
        raise ValueError('reparse points are not allowed')


def _scoped_path(roots, raw, *, directory):
    if not isinstance(raw, str) or not os.path.isabs(raw) or raw.startswith(('\\\\', '//')):
        raise ValueError('local absolute path required')
    candidate = Path(os.path.abspath(raw))
    root = next((r for r in roots if candidate == r or candidate.is_relative_to(r)), None)
    if root is None:
        raise ValueError('path outside profile')
    current = root
    for part in candidate.relative_to(root).parts:
        current = current / part
        _reject_reparse(current)
    path = candidate.resolve(strict=True)
    if not (path == root or path.is_relative_to(root)) or not (path.is_dir() if directory else path.is_file()):
        raise ValueError('path outside profile or wrong type')
    return path


@dataclass(frozen=True)
class Profile:
    name: str
    template: str
    roots: tuple[Path, ...]
    mcp_names: tuple[str, ...] = ()
    mcp_pins: tuple[str, ...] = ()
    mcp_env: tuple = field(default=(), repr=False)


def validate(name, raw):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError('invalid ACP profile')
    if not isinstance(raw, dict) or not {'enabled', 'template', 'workspaceRoots'} <= set(raw) or set(raw) - {'enabled', 'template', 'workspaceRoots', 'stdioMcp'}:
        raise ValueError('invalid ACP profile')
    if raw['enabled'] is not True or not isinstance(raw['template'], str) or not raw['template'].strip():
        raise ValueError('ACP profile disabled or missing template')
    paths = raw['workspaceRoots']
    if not isinstance(paths, list) or not 1 <= len(paths) <= 32:
        raise ValueError('invalid workspace roots')
    roots = []
    for item in paths:
        if not isinstance(item, str) or not os.path.isabs(item) or item.startswith(('\\\\', '//')):
            raise ValueError('absolute workspace roots required')
        root_candidate = Path(os.path.abspath(item))
        for ancestor in reversed((root_candidate, *root_candidate.parents)):
            _reject_reparse(ancestor)
        root = Path(item).resolve(strict=True)
        if not root.is_dir():
            raise ValueError('workspace root is not a directory')
        roots.append(root)
    entries = raw.get('stdioMcp', [])
    if (not isinstance(entries, list) or len(entries) > 16 or any(
            not isinstance(x, dict) or not {'name', 'sha256'} <= set(x) or set(x) - {'name', 'sha256', 'env'}
            or not isinstance(x['name'], str) or not NAME.fullmatch(x['name'])
            or not isinstance(x['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', x['sha256']) for x in entries)):
        raise ValueError('invalid ACP MCP selection')
    names = tuple(x['name'] for x in entries)
    if len(set(names)) != len(names):
        raise ValueError('duplicate ACP MCP selection')
    environments = []
    for item in entries:
        env = item.get('env', {})
        if (not isinstance(env, dict) or len(env) > 64 or any(
                not isinstance(k, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', k)
                or not isinstance(v, str) or '\x00' in v or len(v) > 8192
                for k, v in env.items()) or len({k.upper() for k in env}) != len(env)):
            raise ValueError('invalid ACP MCP environment')
        environments.append(tuple(sorted(env.items())))
    return Profile(name, raw['template'], tuple(roots), names, tuple(x['sha256'] for x in entries), tuple(environments))


def resolve_workspace(profile, cwd):
    if not isinstance(profile, Profile) or not isinstance(cwd, str) or not os.path.isabs(cwd):
        raise ValueError('invalid workspace')
    return _scoped_path(profile.roots, cwd, directory=True)


def resource_text(profile, blocks):
    """Project text and authorized local references without opening a file."""
    if not isinstance(blocks, list) or not blocks or len(blocks) > 128:
        raise ValueError('invalid prompt')
    out = []
    for block in blocks:
        if not isinstance(block, dict):
            raise ValueError('invalid content block')
        if block.get('type') == 'text' and isinstance(block.get('text'), str):
            out.append(block['text'])
            continue
        uri, name = block.get('uri'), block.get('name')
        if (block.get('type') != 'resource_link' or not isinstance(uri, str)
                or not isinstance(name, str) or not name or len(name) > 256
                or any(ord(c) < 32 or ord(c) == 127 for c in name + uri)):
            raise ValueError('invalid resource reference')
        parsed = urlsplit(uri)
        if parsed.scheme.lower() != 'file' or parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError('only local file references are allowed')
        raw = url2pathname(parsed.path)
        if (not os.path.isabs(raw) or raw.startswith(('\\\\', '//'))
                or any(ord(c) < 32 or ord(c) == 127 for c in raw)):
            raise ValueError('absolute local resource path required')
        path = _scoped_path(profile.roots, raw, directory=False)
        out.append('\nResource: ' + name + '\nLocal file: ' + str(path) + '\n')
    result = ''.join(out)
    if not result.strip() or len(result.encode('utf-8')) > 128 * 1024:
        raise ValueError('prompt unavailable or too large')
    return result


def prompt_text(profile, blocks):
    """Accept references without granting access to missing or disallowed files."""
    if not isinstance(blocks, list) or not blocks or len(blocks) > 128:
        raise ValueError('invalid prompt')
    out = []
    for block in blocks:
        if not isinstance(block, dict):
            raise ValueError('invalid content block')
        if block.get('type') == 'text' and isinstance(block.get('text'), str):
            out.append(block['text'])
        elif (block.get('type') == 'resource_link' and isinstance(block.get('uri'), str)
              and isinstance(block.get('name'), str)):
            try:
                out.append(resource_text(profile, [block]))
            except (ValueError, OSError):
                # Do not disclose the URI, name or any content of a resource
                # that the profile cannot access. No network fetch occurs.
                out.append('\n[Resource unavailable]\n')
        else:
            raise ValueError('invalid content block')
    result = ''.join(out)
    if not result.strip() or len(result.encode('utf-8')) > 128 * 1024:
        raise ValueError('prompt unavailable or too large')
    return result


def authorize_mcp(profile, requested, resolve):
    """Match client descriptors exactly to profile-authorized manifest entries."""
    if not isinstance(requested, list) or len(requested) > 16:
        raise ValueError('invalid MCP selection')
    names, canonical = [], []
    for item in requested:
        if (not isinstance(item, dict) or set(item) - {'name', 'command', 'args', 'env'}
                or set(item) != {'name', 'command', 'args', 'env'}):
            raise ValueError('invalid stdio MCP descriptor')
        name, command, args, env = (item[k] for k in ('name', 'command', 'args', 'env'))
        if (name not in profile.mcp_names or name in names or not isinstance(command, str)
                or not os.path.isabs(command) or not isinstance(args, list)
                or any(not isinstance(a, str) for a in args) or not isinstance(env, list)):
            raise ValueError('MCP descriptor not authorized')
        env_dict = {}
        for row in env:
            if (not isinstance(row, dict) or set(row) != {'name', 'value'}
                    or not isinstance(row['name'], str) or not isinstance(row['value'], str)
                    or row['name'] in env_dict):
                raise ValueError('invalid MCP environment')
            env_dict[row['name']] = row['value']
        expected_env = dict(profile.mcp_env[profile.mcp_names.index(name)])
        if env_dict != expected_env:
            raise ValueError('MCP environment not authorized')
        resolved = resolve([name])
        if not isinstance(resolved, list) or len(resolved) != 1:
            raise ValueError('MCP manifest unavailable')
        cfg = resolved[0]
        if (cfg.get('name') != name or cfg.get('command') != command or cfg.get('args', []) != args
                or cfg.get('env', {}) or cfg.get('url') or cfg.get('headers')
                or cfg.get('transport', 'stdio') != 'stdio' or cfg.get('type', 'stdio') != 'stdio'):
            raise ValueError('MCP descriptor not authorized')
        names.append(name)
        canonical.append(cfg)
    digest = hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=True,
                                      separators=(',', ':')).encode()).hexdigest()
    return tuple(names), digest


def mcp_digest(names, resolve):
    entries = []
    for name in names:
        row = resolve([name])
        if not isinstance(row, list) or len(row) != 1:
            raise ValueError('MCP manifest unavailable')
        entries.append(row[0])
    return hashlib.sha256(json.dumps(entries, sort_keys=True, ensure_ascii=True,
                                     separators=(',', ':')).encode()).hexdigest()


def physical_mcp_pin(catalog, names):
    physical = catalog.compile(catalog.legacy_bindings(list(names)))
    return hashlib.sha256(json.dumps(physical, sort_keys=True, ensure_ascii=True,
                                     separators=(',', ':')).encode()).hexdigest()
