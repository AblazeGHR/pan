import os
import pytest
from packages.acp.policy import validate, resolve_workspace


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
