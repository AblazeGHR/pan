import os
import pytest
from packages.acp.policy import validate, resolve_workspace, resource_text, authorize_mcp, mcp_digest


def test_profile_defaults_closed_and_workspace_is_canonical(tmp_path):
    allowed = tmp_path / 'allowed'
    allowed.mkdir()
    child = allowed / 'child'
    child.mkdir()
    profile = validate('research', {'enabled':True, 'template':'fixture', 'workspaceRoots':[str(allowed)]})
    assert resolve_workspace(profile, str(child)) == child.resolve()
    with pytest.raises(ValueError): resolve_workspace(profile, str(tmp_path))
    with pytest.raises(ValueError): resolve_workspace(profile, 'relative')
    for value in ({'enabled':False, 'template':'fixture', 'workspaceRoots':[str(allowed)]},
                  {'enabled':True, 'template':'', 'workspaceRoots':[str(allowed)]},
                  {'enabled':True, 'template':'fixture', 'workspaceRoots':[]},
                  {'enabled':True, 'template':'fixture', 'workspaceRoots':[str(allowed)], 'extra':True}):
        with pytest.raises(ValueError): validate('research', value)


def test_workspace_symlink_escape_rejected(tmp_path):
    allowed, outside = tmp_path / 'allowed', tmp_path / 'outside'
    allowed.mkdir(); outside.mkdir()
    link = allowed / 'link'
    try: os.symlink(outside, link, target_is_directory=True)
    except OSError: pytest.skip('OS does not permit creating test symlinks')
    profile = validate('research', {'enabled':True, 'template':'fixture', 'workspaceRoots':[str(allowed)]})
    with pytest.raises(ValueError): resolve_workspace(profile, str(link))


def test_local_resource_scope_and_untrusted_uri(tmp_path):
    root, outside = tmp_path / 'root', tmp_path / 'outside'
    root.mkdir(); outside.mkdir()
    f = root / 'paper.txt'; f.write_text('private contents')
    other = outside / 'other.txt'; other.write_text('secret')
    profile = validate('p', {'enabled':True, 'template':'t', 'workspaceRoots':[str(root)]})
    text = resource_text(profile, [{'type':'text','text':'Read'}, {'type':'resource_link','uri':f.as_uri(),'name':'paper'}])
    assert str(f.resolve()) in text and 'private contents' not in text
    for uri in (other.as_uri(), 'https://example.test/file', 'file://remote/share/file', 'file:////remote/share/file', f.as_uri() + '?x=1',
                (root / 'missing').as_uri()):
        with pytest.raises((ValueError, OSError)):
            resource_text(profile, [{'type':'resource_link','uri':uri,'name':'x'}])


def test_stdio_mcp_exact_trusted_manifest_matching(tmp_path):
    profile = validate('p', {'enabled':True, 'template':'t', 'workspaceRoots':[str(tmp_path)],
                             'stdioMcp':[{'name':'tool', 'sha256':'0'*64}]})
    command = str(tmp_path / 'program.exe')
    cfg = {'name':'tool','command':command,'args':['--fixed']}
    def resolve(names): return [cfg] if names == ['tool'] else []
    requested = {'name':'tool','command':command,'args':['--fixed'], 'env':[]}
    names, digest = authorize_mcp(profile, [requested], resolve)
    assert names == ('tool',) and digest == mcp_digest(names, resolve)
    for bad in ({**requested, 'args':['--other']}, {**requested,'command':command + 'x'},
                {**requested,'name':'other'}, {**requested,'env':[{'name':'SETTING','value':'value'}]},
                {**requested,'env':[{'name':'SETTING','value':'value'}, {'name':'SETTING','value':'value'}]},
                {**requested,'url':'http://localhost'}):
        with pytest.raises(ValueError): authorize_mcp(profile, [bad], resolve)


def test_mcp_manifest_changes_before_creation_are_rejected(tmp_path):
    import asyncio
    from types import SimpleNamespace
    from packages.acp.pan_service import bind
    from packages.acp.policy import physical_mcp_pin
    async def run():
        cfg = {'name':'tool', 'command':str(tmp_path / 'tool.exe'), 'args':[]}
        class Catalog:
            def legacy_bindings(self, names): return names
            def compile(self, names): return [dict(cfg)]
        catalog = Catalog()
        profile = validate('p', {'enabled':True, 'template':'t', 'workspaceRoots':[str(tmp_path)],
                                 'stdioMcp':[{'name':'tool', 'sha256':physical_mcp_pin(catalog, ['tool'])}]})
        created = []
        async def create(data): created.append(data); return {'id':'s1'}
        server = SimpleNamespace(_resolve_mcp_server_configs=lambda _: [dict(cfg)], api_create_session=create,
                                 _plugin_catalog=lambda:catalog,
                                 _character_manager=SimpleNamespace(get_session_template=lambda _:SimpleNamespace(adapter='cbc')))
        service = bind(server, lambda _:profile)
        handle = service.prepare('p', str(tmp_path), [{'name':'tool','command':cfg['command'],'args':[],'env':[]}])
        cfg['args'] = ['changed']
        with pytest.raises(PermissionError):
            await service.submit('p', handle, 'turn', 'hello')
        assert created == []
    asyncio.run(run())


def test_pinned_worker_mcp_descriptor_change_fails_closed():
    import hashlib
    import json
    from types import SimpleNamespace
    from packages.core.adapters.mcp import build_mcp_servers
    rows = [{'name':'tool', 'command':'tool.exe', 'args':['safe']}]
    pin = hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=True,
                                    separators=(',', ':')).encode()).hexdigest()
    session = SimpleNamespace(adapter_config={'mcp_servers':rows, '_acp_mcp_digest':pin})
    assert build_mcp_servers(session)['tool']['args'] == ['safe']
    rows[0]['args'] = ['changed']
    with pytest.raises(ValueError, match='configuration changed'):
        build_mcp_servers(session)
